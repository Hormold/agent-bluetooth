"""Recording bridge: saves each call's audio to a WAV file. Proves the SCO
audio path and doubles as voicemail/call-recording. No room."""

from __future__ import annotations

import asyncio
import logging
import time
import wave
from pathlib import Path
from typing import Any

from ...core.context import Context
from ...core.events import E
from ...core.ports import PCM_BYTES_PER_SAMPLE, AudioPipe, Call

log = logging.getLogger(__name__)


class RecorderBridge:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.bus = ctx.bus
        self.dir = Path(cfg.get("dir", "./recordings")).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None: ...

    async def stop(self) -> None:
        for t in self._tasks.values():
            t.cancel()

    async def open(self, call: Call) -> dict[str, Any]:
        return {"room": None}

    async def attach_audio(self, call: Call, audio: AudioPipe) -> None:
        digits = "".join(c for c in call.number if c.isdigit()) or "unknown"
        path = self.dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{call.direction}-{digits}.wav"
        self._tasks[call.id] = asyncio.create_task(self._record(call, audio, path))

    async def close(self, call: Call) -> None:
        if t := self._tasks.pop(call.id, None):
            t.cancel()

    def join_info(self, room: str, identity: str) -> dict[str, Any]:
        return {}

    async def _record(self, call: Call, audio: AudioPipe, path: Path) -> None:
        w = wave.open(str(path), "wb")
        w.setnchannels(1)
        w.setsampwidth(PCM_BYTES_PER_SAMPLE)
        w.setframerate(audio.sample_rate)
        frames = 0
        log.info("recording call %s -> %s", call.id, path)
        try:
            while (pcm := await audio.read()) is not None:
                w.writeframes(pcm)
                frames += len(pcm) // PCM_BYTES_PER_SAMPLE
        finally:
            w.close()
            secs = frames / audio.sample_rate
            log.info("saved %s (%.1fs)", path, secs)
            self.bus.publish(E.RECORDING_SAVED, call_id=call.id, file=str(path), seconds=round(secs, 1))
