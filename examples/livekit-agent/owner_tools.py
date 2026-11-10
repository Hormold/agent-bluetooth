"""What Agent Phone (the Mac app) understands, as a mixin for any LiveKit agent.

    class MyAgent(OwnerTools, Agent): ...

gives the model an `ask_owner` tool and the agent three calls:

- `ask_owner(question)` (a tool): a question card pops up in Agent Phone
  (window to the front, red frame, sound, banner). The owner's typed answer
  comes back as the tool result. Nothing is said on the line.
- `call_owner_in(text)`: "A person is on the line" with a mic-on button.
- `clear_attention()`: takes the card down.

Owner lines (the app's Say box, `agent-bluetooth say`) arrive as data messages
on topic "owner". Route each one through `take_owner_line(text)` first: it
returns True when the line answered an open question; otherwise the line is
yours to put on the call. `serve_owner()` in agent.py delivers them to
`owner_says()`.

Wire format and app behaviour: docs/OWNER.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

import aiohttp
from livekit.agents import function_tool, get_job_context

if TYPE_CHECKING:
    from collections.abc import Coroutine

log = logging.getLogger("owner-tools")

DAEMON = os.getenv("AGENT_BLUETOOTH_URL", "http://127.0.0.1:8080")
OWNER = os.getenv("OWNER_NAME", "the owner")
ASK_TIMEOUT = float(os.getenv("ASK_TIMEOUT", "180"))  # seconds to wait for an answer


class OwnerTools:
    """Mixin: put it before `Agent` in the bases."""

    _pending: asyncio.Future[str] | None = None
    _owner_tasks: set[asyncio.Task[Any]]

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Fire-and-forget work that must not block a LiveKit callback."""
        if not hasattr(self, "_owner_tasks"):
            self._owner_tasks = set()
        task = asyncio.get_running_loop().create_task(coro)
        self._owner_tasks.add(task)
        task.add_done_callback(self._owner_tasks.discard)

    # the alert card ------------------------------------------------------------
    def attention(self, kind: str, text: str = "") -> None:
        """person | question | clear -> a call.attention event; Agent Phone alerts."""
        self.spawn(post_attention(kind, text))

    def call_owner_in(self, text: str = "") -> None:
        self.attention("person", text[:200])

    def clear_attention(self) -> None:
        self.attention("clear")

    # questions -----------------------------------------------------------------
    @property
    def asking(self) -> bool:
        return self._pending is not None and not self._pending.done()

    def take_owner_line(self, text: str) -> bool:
        """True when `text` answered the open question (don't say it on the line)."""
        if not self.asking or self._pending is None:
            return False
        self._pending.set_result(text)
        return True

    async def wait_for_owner(self, question: str) -> str | None:
        """Raise the question card and wait for the answer; None on timeout."""
        self._pending = asyncio.get_running_loop().create_future()
        self.attention("question", question)
        try:
            async with asyncio.timeout(ASK_TIMEOUT):
                return await self._pending
        except TimeoutError:
            return None
        finally:
            self._pending = None
            self.clear_attention()

    @function_tool
    async def ask_owner(self, question: str) -> str:
        """Silently ask the owner for a fact you don't have (an account number,
        a preference, a yes/no). Blocks until they answer and returns it. While
        waiting, say nothing to a machine and only "one moment please" to a person.

        Args:
            question: One short question for the owner.
        """
        log.info("asking the owner: %s", question)
        answer = await self.wait_for_owner(question)
        if answer is None:
            return f"{OWNER} did not answer. Ask the person to hold, or move on."
        return f"{OWNER} says: {answer}"


async def post_attention(kind: str, text: str = "") -> None:
    try:
        room = get_job_context().room.name
        async with aiohttp.ClientSession() as http:
            await http.post(
                f"{DAEMON}/api/calls/attention", json={"kind": kind, "text": text, "room": room}
            )
    except Exception:  # an alert must never break the call
        log.exception("attention not delivered")
