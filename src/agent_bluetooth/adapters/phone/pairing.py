"""Pairing window: discoverable for N seconds, then back to the radio's
baseline ([bluetooth] discoverable). A new window replaces the running one, so
calling pair again resets the timer. Connectable never changes: bonded phones
must always be able to reconnect."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ...core.tasks import Tasks

if TYPE_CHECKING:
    from ..bumble.radio import BumbleRadio

log = logging.getLogger(__name__)
MAX_WINDOW_S = 3600  # an hour is plenty to pick a device in phone settings
HCI_TIMEOUT = 5.0  # s for one discoverable on/off command


class PairingWindow:
    def __init__(self) -> None:
        self.until: float | None = None  # wall-clock end of the open window
        self._tasks = Tasks("pairing-window")
        self._task: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return {"open": self.until is not None, "until": self.until}

    async def open(self, radio: BumbleRadio, seconds: float) -> float:
        """Go discoverable now; returns the wall-clock time the window closes."""
        if not 0 < seconds <= MAX_WINDOW_S:
            raise ValueError(f"seconds must be in (0, {MAX_WINDOW_S}], got {seconds:g}")
        self.cancel()  # a new pair call resets the timer
        await asyncio.wait_for(radio.set_discoverable(on=True), HCI_TIMEOUT)
        self.until = time.time() + seconds
        self._task = self._tasks.spawn(self._close_after(radio, seconds), name="pairing-window")
        log.info("discoverable for %.0fs (pairing window)", seconds)
        return self.until

    def cancel(self) -> None:
        """Drop the timer without touching the radio (daemon stopping, or a new window)."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.until = None

    async def aclose(self) -> None:
        """Daemon stopping: drop the timer and wait for it to be gone."""
        self.cancel()
        await self._tasks.aclose()

    async def _close_after(self, radio: BumbleRadio, seconds: float) -> None:
        await asyncio.sleep(seconds)
        self.until = None
        try:
            if radio.discoverable_baseline:
                log.info("pairing window over; staying discoverable ([bluetooth] discoverable)")
                return
            # Still the window's task while switching off, so a new pair call
            # cancels this instead of racing it.
            await asyncio.wait_for(radio.set_discoverable(on=False), HCI_TIMEOUT)
            log.info("pairing window over; no longer discoverable")
        finally:
            if self._task is asyncio.current_task():
                self._task = None
