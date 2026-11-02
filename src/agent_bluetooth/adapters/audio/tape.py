"""Call recording at the transport: what the phone line carried, both ways.

Taps the AudioPipe where audio enters and leaves the line, so it records any
call the same way (an agent, you in the app, a greeting) and hears exactly
what went out, cushion and padding included. Stereo WAV: left = the other
side, right = this side.
"""

from __future__ import annotations

import array
import wave
from pathlib import Path

from ...core.ports import PCM_BYTES_PER_SAMPLE

FLUSH_SECONDS = 1.0
MAX_SKEW_SECONDS = 1.0  # one side went quiet on the pipe: pad it, keep in sync


class CallTape:
    def __init__(self, path: Path, sample_rate: int) -> None:
        self.path = path
        self.rate = sample_rate
        self._heard = bytearray()
        self._sent = bytearray()
        self._out = bytearray()
        self.frames = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(2)
        self._wav.setsampwidth(PCM_BYTES_PER_SAMPLE)
        self._wav.setframerate(sample_rate)

    # AudioTap -------------------------------------------------------------
    def heard(self, pcm: bytes) -> None:
        self._heard += pcm
        self._mix()

    def sent(self, pcm: bytes) -> None:
        self._sent += pcm
        self._mix()

    # ----------------------------------------------------------------------
    @property
    def seconds(self) -> float:
        return self.frames / self.rate

    def close(self) -> None:
        self._mix(final=True)
        self._flush()
        self._wav.close()

    def _mix(self, final: bool = False) -> None:
        skew = int(self.rate * MAX_SKEW_SECONDS) * PCM_BYTES_PER_SAMPLE
        for a, b in ((self._heard, self._sent), (self._sent, self._heard)):
            if final or len(a) - len(b) > skew:
                b += bytes(max(0, len(a) - len(b)))
        n = min(len(self._heard), len(self._sent)) // PCM_BYTES_PER_SAMPLE
        if not n:
            return
        size = n * PCM_BYTES_PER_SAMPLE
        stereo = array.array("h", bytes(2 * size))
        stereo[0::2] = array.array("h", self._heard[:size])
        stereo[1::2] = array.array("h", self._sent[:size])
        del self._heard[:size], self._sent[:size]
        self._out += stereo.tobytes()
        self.frames += n
        if len(self._out) >= self.rate * 2 * PCM_BYTES_PER_SAMPLE * FLUSH_SECONDS:
            self._flush()

    def _flush(self) -> None:
        if self._out:
            self._wav.writeframes(self._out)
            self._out.clear()
