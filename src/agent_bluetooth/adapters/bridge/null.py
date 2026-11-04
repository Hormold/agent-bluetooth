"""No-op bridge: no room, call audio discarded. For headless / notification-only
setups (events + SMS + automations) and for bring-up before LiveKit is wired."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...core.tasks import Tasks

if TYPE_CHECKING:
    import asyncio

    from ...core.context import Context
    from ...core.ports import AudioPipe, Call


class NullBridge:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self._tasks = Tasks(f"{ctx.bus.instance}:null-bridge")
        self._drains: dict[str, tuple[AudioPipe, asyncio.Task[None]]] = {}

    async def start(self) -> None: ...

    async def stop(self) -> None:
        self._drains.clear()
        await self._tasks.aclose()

    async def open(self, call: Call, *, agent_name: str = "") -> dict[str, Any]:
        return {"room": None}  # no room, so no agent to dispatch either

    async def attach_audio(self, call: Call, audio: AudioPipe) -> None:
        current = self._drains.get(call.id)
        if current and current[0] is audio and not current[1].done():
            return  # audio-up is announced more than once per link
        await self.close(call)
        drain = self._tasks.spawn(self._drain(audio), name=f"null-drain:{call.id}")
        self._drains[call.id] = (audio, drain)

    async def close(self, call: Call) -> None:
        if drain := self._drains.pop(call.id, None):
            drain[1].cancel()

    def join_info(self, room: str, identity: str) -> dict[str, Any]:
        return {}

    @staticmethod
    async def _drain(audio: AudioPipe) -> None:
        while await audio.read() is not None:  # keep the SCO clock fed with silence
            pass
