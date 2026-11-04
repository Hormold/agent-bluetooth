"""`agent-bluetooth farm`: run every instance of a config, each in its own process.

One process per dongle is the isolation boundary. A daemon that loses its radio
exits on purpose (see core/watchdog.fatal) and only that one restarts; the
others keep their calls. This supervisor is for hosts without systemd (macOS, a
dev box). On Linux, the `agent-bluetooth@.service` template does the same job
with the watchdog on top (see docs/FARM.md).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .core.instance import names
from .core.tasks import Tasks

if TYPE_CHECKING:
    from typing import Any

log = logging.getLogger("agent_bluetooth.farm")
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
HEALTHY_AFTER = 60.0  # a run this long resets the backoff
STOP_GRACE = 10.0  # the daemon itself time-boxes shutdown to 6 s


@dataclass
class _Child:
    name: str
    argv: list[str]
    restarts: int = 0
    backoff: float = BACKOFF_START


async def _pump(stream: asyncio.StreamReader) -> None:
    """Child output to ours, line by line (each line already carries [name])."""
    while line := await stream.readline():
        sys.stdout.write(line.decode(errors="replace"))
        sys.stdout.flush()


async def _terminate(name: str, proc: asyncio.subprocess.Process) -> None:
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), STOP_GRACE)
    except TimeoutError:
        log.warning("instance %s ignored SIGTERM; killing", name)
        proc.kill()
        await proc.wait()


async def _run_once(child: _Child, stopping: asyncio.Event) -> int | None:
    """One run of the daemon; on farm shutdown, stop it and wait for it."""
    proc = await asyncio.create_subprocess_exec(
        *child.argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    tasks = Tasks(f"farm:{child.name}")
    try:
        pump = tasks.spawn(_pump(proc.stdout), name="pump") if proc.stdout else None
        exited = tasks.spawn(proc.wait(), name="exited")
        stop = tasks.spawn(stopping.wait(), name="stopping")
        await asyncio.wait({exited, stop}, return_when=asyncio.FIRST_COMPLETED)
        if not exited.done():
            await _terminate(child.name, proc)
        if pump:
            await pump  # the child's last lines, up to its exit
    finally:
        await tasks.aclose()  # the stop watch, or everything if we were cancelled
    return proc.returncode


async def _keep_running(child: _Child, stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        started = time.monotonic()
        code = await _run_once(child, stopping)
        if stopping.is_set():
            return
        ran = time.monotonic() - started
        child.backoff = (
            BACKOFF_START if ran > HEALTHY_AFTER else min(child.backoff * 2, BACKOFF_MAX)
        )
        child.restarts += 1
        log.warning(
            "instance %s exited (%s) after %.0fs; restart #%d in %.0fs",
            child.name,
            code,
            ran,
            child.restarts,
            child.backoff,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), child.backoff)


async def supervise(
    config_path: str, raw: dict[str, Any], only: list[str], *, verbose: bool
) -> None:
    selected = only or names(raw)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    base = [sys.executable, "-m", "agent_bluetooth", "serve", config_path]
    children = [_Child(n, [*base, "--instance", n, *(["-v"] if verbose else [])]) for n in selected]
    log.info("farm: %s", ", ".join(selected))
    await asyncio.gather(*(_keep_running(c, stopping) for c in children))
    log.info("farm: stopped")
