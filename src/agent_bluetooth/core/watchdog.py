"""Liveness watchdog. Radio stacks wedge (stuck USB poll, hung controller,
GIL starvation); when that happens the event loop stops making progress and no
in-process code can fix it. So we lean on systemd: ping it while healthy, and
if the pings stop it kills and restarts us.

Two layers:
  - sd_notify(): talk to systemd's WatchdogSec (Type=notify). If our heartbeat
    task can't run (loop blocked/GIL-starved), systemd restarts the process.
  - a progress deadline: the heartbeat only fires while the loop is actually
    cycling, so a silent deadlock is caught too.

With no systemd (plain `serve`), this is a no-op beyond a log line.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

log = logging.getLogger(__name__)


def sd_notify(state: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr[0] == "@":  # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(state.encode(), addr)
        return True
    except OSError:
        log.debug("sd_notify failed", exc_info=True)
        return False


def watchdog_interval() -> float:
    """Ping at a third of systemd's WatchdogSec (µs), default 10 s."""
    usec = os.environ.get("WATCHDOG_USEC")
    return max(1.0, int(usec) / 1_000_000 / 3) if usec else 10.0


async def heartbeat() -> None:
    sd_notify("READY=1")
    interval = watchdog_interval()
    if os.environ.get("WATCHDOG_USEC"):
        log.info("systemd watchdog active; heartbeat every %.0fs", interval)
    while True:
        await asyncio.sleep(interval)
        sd_notify("WATCHDOG=1")
