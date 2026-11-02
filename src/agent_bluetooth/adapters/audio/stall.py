"""Keep a caller busy with recorded phrases ("Hello? Sorry, can you repeat that?").

For spammers: answer, then play a random phrase from a folder, wait until the
caller has said something and gone quiet (or a few seconds pass), play the next
one, and so on until the time limit, then hang up. Same AudioHandler contract as
FilePlayer. Put recordings of your own voice in the folder for best effect.
"""

from __future__ import annotations

import array
import asyncio
import logging
import math
import random
from pathlib import Path

from ...core.bus import EventBus
from ...core.events import E
from ...core.ports import PCM_BYTES_PER_SAMPLE, AudioPipe, Call
from .file_player import FRAME_MS, _load_wav

log = logging.getLogger(__name__)
SPEECH_RMS = 400  # caller speaking (16-bit PCM, phone line)
QUIET_AFTER_S = 0.8  # this much silence after speech = their turn is over
NO_REPLY_S = 6.0  # nobody spoke: say the next line anyway
MAX_TURN_S = 12.0  # don't wait forever for a monologue to end


def phrases_in(folder: str) -> list[Path]:
    return sorted(p for p in Path(folder).expanduser().glob("*.wav") if p.is_file())


class StallPlayer:
    def __init__(self, bus: EventBus, folder: str, max_minutes: float = 5) -> None:
        self.bus = bus
        self.folder = folder
        self.max_seconds = max_minutes * 60
        self._task: asyncio.Task | None = None

    async def attach_audio(self, call: Call, audio: AudioPipe) -> None:
        if self._task and not self._task.done():
            return  # audio-up is announced twice per call
        clips = [_load_wav(str(p), audio.sample_rate) for p in phrases_in(self.folder)]
        if not clips:
            log.warning("stall: no .wav phrases in %s; hanging up", self.folder)
            self.bus.publish(E.PLAYBACK_DONE, call_id=call.id, file=self.folder)
            return
        self._task = asyncio.create_task(self._run(call, audio, clips))

    async def close(self, call: Call) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self, call: Call, audio: AudioPipe, clips: list[bytes]) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + self.max_seconds
        last = -1
        turns = 0
        log.info(
            "stalling call %s with %d phrases for %.0fs",
            call.id,
            len(clips),
            self.max_seconds,
        )
        while loop.time() < end:
            i = random.choice([n for n in range(len(clips)) if n != last] or [0])
            last = i
            await self._say(audio, clips[i])
            await self._listen(audio, min(end, loop.time() + MAX_TURN_S))
            turns += 1
        log.info("stall done on %s after %d phrases", call.id, turns)
        self.bus.publish(E.PLAYBACK_DONE, call_id=call.id, file=self.folder)

    @staticmethod
    async def _say(audio: AudioPipe, pcm: bytes) -> None:
        frame = audio.sample_rate * FRAME_MS // 1000 * PCM_BYTES_PER_SAMPLE
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        for off in range(0, len(pcm), frame):
            audio.write(pcm[off : off + frame])
            audio.read_discard()  # don't treat our own phrase as their reply
            next_at += FRAME_MS / 1000
            await asyncio.sleep(max(0, next_at - loop.time()))
        await asyncio.sleep(0.3)  # let the line cushion drain
        audio.read_discard()

    @staticmethod
    async def _listen(audio: AudioPipe, deadline: float) -> None:
        """Return once the caller spoke and then went quiet, or nobody spoke."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        spoke = False
        quiet_since = loop.time()
        while loop.time() < deadline:
            chunk = audio.read_nowait()
            if chunk is None:
                return  # call audio closed
            if chunk:
                if _rms(chunk) >= SPEECH_RMS:
                    spoke = True
                    quiet_since = loop.time()
                elif spoke and loop.time() - quiet_since >= QUIET_AFTER_S:
                    return
                continue
            if not spoke and loop.time() - started >= NO_REPLY_S:
                return
            await asyncio.sleep(FRAME_MS / 1000)


def _rms(pcm: bytes) -> float:
    samples = array.array("h", pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))
