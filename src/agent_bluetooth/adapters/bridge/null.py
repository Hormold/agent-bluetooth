"""No-op bridge: no room, call audio discarded. For headless / notification-only
setups (events + SMS + automations) and for bring-up before LiveKit is wired."""

from __future__ import annotations

import asyncio
from typing import Any

from ...core.context import Context
from ...core.ports import AudioPipe, Call


class NullBridge:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None: ...

    async def stop(self) -> None:
        for t in self._tasks.values():
            t.cancel()

    async def open(self, call: Call) -> dict[str, Any]:
        return {"room": None}

    async def attach_audio(self, call: Call, audio: AudioPipe) -> None:
        self._tasks[call.id] = asyncio.create_task(self._drain(audio))

    async def close(self, call: Call) -> None:
        if t := self._tasks.pop(call.id, None):
            t.cancel()

    def join_info(self, room: str, identity: str) -> dict[str, Any]:
        return {}

    @staticmethod
    async def _drain(audio: AudioPipe) -> None:
        while await audio.read() is not None:  # keep the SCO clock fed with silence
            pass
