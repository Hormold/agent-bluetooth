"""Background tasks with an owner that ends them.

asyncio keeps only a weak reference to a task, so a bare `create_task(...)` whose
result is dropped may vanish before it finishes, and nothing cancels it when its
owner goes away. Every component that starts tasks owns a `Tasks`: `spawn` holds
a strong reference until the task is done and logs any exception instead of
losing it; the owner's stop/close calls `aclose`, which cancels and awaits them.
This is the only module allowed to call `asyncio.create_task` (ruff TID251).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine

log = logging.getLogger(__name__)
T = TypeVar("T")


class Tasks:
    def __init__(self, owner: str) -> None:
        self.owner = owner  # names the owner in logs: "<instance>:router", "farm:one", ...
        self._live: set[asyncio.Task[object]] = set()

    def __len__(self) -> int:
        return len(self._live)

    def spawn(self, coro: Coroutine[Any, Any, T], *, name: str) -> asyncio.Task[T]:
        task = asyncio.create_task(coro, name=name)
        self._live.add(task)
        task.add_done_callback(self._reap)
        return task

    async def aclose(self, timeout: float = 5.0) -> None:  # noqa: ASYNC109 - the grace each cancelled task gets, not a deadline for the caller
        """Cancel every live task (except the one calling this) and wait up to
        `timeout` s for them to finish. Tasks spawned while closing (a finally
        block starting a cleanup) are cancelled too. The owner may spawn again
        afterwards: closing ends what runs now, it doesn't retire the owner."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        me = asyncio.current_task()
        while pending := {t for t in self._live if t is not me and not t.done()}:
            for task in pending:
                task.cancel()
            left = deadline - loop.time()
            if left <= 0:
                break
            await asyncio.wait(pending, timeout=left)
        for task in self._live:
            if task is not me and not task.done():
                log.warning(
                    "%s: task %s still running after %.1fs", self.owner, task.get_name(), timeout
                )

    def _reap(self, task: asyncio.Task[object]) -> None:
        self._live.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.error("%s: task %s failed", self.owner, task.get_name(), exc_info=exc)
