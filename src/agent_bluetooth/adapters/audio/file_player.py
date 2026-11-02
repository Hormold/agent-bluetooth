"""Plays a WAV file into a live call (voicemail greeting, IVR prompt, bot audio).

An AudioHandler, interchangeable with the LiveKit bridge: the router attaches
it instead of a room. 16-bit PCM WAV, any sample rate (resampled to the line),
mono or stereo. Optionally loops, and/or hangs up when the clip ends.
"""

from __future__ import annotations

import array
import asyncio
import logging
import wave

from ...core.bus import EventBus
from ...core.events import E
from ...core.ports import PCM_BYTES_PER_SAMPLE, AudioPipe, Call

log = logging.getLogger(__name__)
FRAME_MS = 20


class FilePlayer:
    def __init__(
        self, bus: EventBus, path: str, loop: bool = False, hangup_after: bool = True
    ) -> None:
        self.bus = bus
        self.path = path
        self.loop = loop
        self.hangup_after = hangup_after
        self._task: asyncio.Task | None = None

    async def attach_audio(self, call: Call, audio: AudioPipe) -> None:
        pcm = _load_wav(self.path, audio.sample_rate)
        self._task = asyncio.create_task(self._play(call, audio, pcm))

    async def close(self, call: Call) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _play(self, call: Call, audio: AudioPipe, pcm: bytes) -> None:
        frame = audio.sample_rate * FRAME_MS // 1000 * PCM_BYTES_PER_SAMPLE
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        log.info("playing %s into call %s", self.path, call.id)
        while True:
            for off in range(0, len(pcm), frame):
                audio.write(pcm[off : off + frame])
                audio.read_discard()  # drop caller audio so it can't echo the clip back
                next_at += FRAME_MS / 1000
                await asyncio.sleep(max(0, next_at - loop.time()))
            if not self.loop:
                break
        self.bus.publish(
            E.CALL_AUDIO,
            call=call.to_dict(),
            up=True,
            sample_rate=audio.sample_rate,
            played=self.path,
        )
        if self.hangup_after:
            self.bus.publish(E.PLAYBACK_DONE, call_id=call.id, file=self.path)


def _load_wav(path: str, target_rate: int) -> bytes:
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != PCM_BYTES_PER_SAMPLE:
            raise ValueError(
                f"{path}: need 16-bit PCM WAV, got {w.getsampwidth() * 8}-bit"
            )
        channels, rate = w.getnchannels(), w.getframerate()
        samples = array.array("h", w.readframes(w.getnframes()))
    if channels > 1:  # downmix to mono
        samples = array.array(
            "h",
            (
                sum(samples[i : i + channels]) // channels
                for i in range(0, len(samples), channels)
            ),
        )
    if rate != target_rate:  # linear resample
        samples = _resample(samples, rate, target_rate)
    return samples.tobytes()


def _resample(samples: array.array[int], src: int, dst: int) -> array.array[int]:
    n = len(samples) * dst // src
    out = array.array("h", bytes(n * PCM_BYTES_PER_SAMPLE))
    for i in range(n):
        pos = i * src / dst
        j = int(pos)
        frac = pos - j
        a = samples[j]
        b = samples[j + 1] if j + 1 < len(samples) else a
        out[i] = int(a + (b - a) * frac)
    return out
