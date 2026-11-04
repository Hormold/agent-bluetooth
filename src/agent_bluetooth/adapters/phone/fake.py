"""A pretend phone. Lets you run the whole stack with no Bluetooth hardware:
simulate an incoming call, hear a tone in the room, see the level of what the
room sends back. Also the reference for writing a new Phone adapter."""

from __future__ import annotations

import array
import asyncio
import math
from typing import TYPE_CHECKING, Any, ClassVar

from ...core.errors import PhoneCommandError
from ...core.events import E
from ...core.ports import PCM_BYTES_PER_SAMPLE, AudioPipe, Call
from ...core.tasks import Tasks

if TYPE_CHECKING:
    from ...core.context import Context
    from ...core.errors import Reason

FRAME_MS = 20
BEEP_ON = 0.3  # seconds of tone at the start of every second
COMMANDS = ("answer", "hangup", "dial", "dtmf", "volume")
REASONS: tuple[Reason, ...] = ("timeout", "rejected", "link")


class FakePhone:
    simulates: ClassVar[frozenset[str]] = frozenset({"call", "remote_hangup", "fail"})

    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.bus = ctx.bus
        self.sample_rate = int(cfg.get("sample_rate", 8000))
        self.call: Call | None = None
        self.pipe: AudioPipe | None = None
        self._tasks = Tasks(f"{ctx.bus.instance}:fake-phone")
        self._audio_task: asyncio.Task[None] | None = None
        self.rx_level = 0.0  # loudness of audio the room sends to the "phone"
        self._fail: dict[str, Reason] = {}  # armed by `sim fail`: next <cmd> fails once

    async def start(self) -> None:
        self.bus.publish(E.PHONE_CONNECTED, name="Fake Phone", address="00:00:00:00:00:00")

    async def stop(self) -> None:
        await self._end()
        await self._tasks.aclose()

    def status(self) -> dict[str, Any]:
        return {
            "adapter": "fake",
            "connected": True,
            "call": self.call.to_dict() if self.call else None,
            "rx_level": round(self.rx_level, 3),
            "failing": dict(self._fail),
        }

    def audio(self) -> AudioPipe | None:
        return self.pipe

    # commands --------------------------------------------------------------
    async def answer(self) -> None:
        self._maybe_fail("answer")
        if self.call and self.call.state == "ringing":
            self._activate()

    async def hangup(self) -> None:
        self._maybe_fail("hangup")
        await self._end()

    async def dial(self, number: str) -> None:
        self._maybe_fail("dial")
        self.call = Call(direction="out", number=number, state="dialing")
        self.bus.publish(E.CALL_DIALING, call=self.call.to_dict())
        await asyncio.sleep(2)  # the other side picks up
        if self.call and self.call.state == "dialing":
            self._activate()

    async def send_dtmf(self, digits: str) -> None:
        self._maybe_fail("dtmf")

    async def set_volume(self, level: int) -> None:
        self._maybe_fail("volume")
        self.bus.publish(E.PHONE_STATUS, speaker_volume=level)

    # dev -------------------------------------------------------------------
    async def simulate(
        self,
        kind: str,
        number: str = "+15551234567",
        name: str = "",
        cmd: str = "",
        reason: str = "rejected",
        **_: object,
    ) -> None:
        if kind == "fail":
            self._arm_failure(cmd, reason)
        elif kind == "call":
            self.call = Call(direction="in", number=number, name=name)
            self.bus.publish(E.CALL_RINGING, call=self.call.to_dict())
        elif kind == "remote_hangup":
            await self._end()

    # internals -------------------------------------------------------------
    def _arm_failure(self, cmd: str, reason: str) -> None:
        """`sim fail cmd=hangup [reason=timeout|rejected|link]`: the next such
        command fails the way a real phone's would."""
        if cmd not in COMMANDS:
            raise ValueError(f"cmd must be one of {', '.join(COMMANDS)}")
        for r in REASONS:
            if r == reason:
                self._fail[cmd] = r
                return
        raise ValueError(f"reason must be one of {', '.join(REASONS)}")

    def _maybe_fail(self, cmd: str) -> None:
        if (reason := self._fail.pop(cmd, None)) is not None:
            raise PhoneCommandError(cmd, reason, "simulated failure")

    def _activate(self) -> None:
        if self.call is None:
            raise RuntimeError("no call to activate")
        self.call.state = "active"
        self.bus.publish(E.CALL_ACTIVE, call=self.call.to_dict())
        self.pipe = AudioPipe(self.sample_rate)
        self._audio_task = self._tasks.spawn(self._audio_loop(self.pipe), name="fake-audio")
        self.bus.publish(
            E.CALL_AUDIO,
            call=self.call.to_dict(),
            up=True,
            sample_rate=self.sample_rate,
        )

    async def _end(self) -> None:
        if self._audio_task:
            self._audio_task.cancel()
            self._audio_task = None
        if self.pipe:
            self.pipe.close()
            self.pipe = None
        if self.call:
            self.call.state = "ended"
            self.bus.publish(E.CALL_ENDED, call=self.call.to_dict())
            self.call = None

    async def _audio_loop(self, pipe: AudioPipe) -> None:
        """Plays a 440 Hz beep every second, like a caller who won't stop."""
        samples = self.sample_rate * FRAME_MS // 1000
        n = 0
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while True:
            frame = array.array("h", bytes(samples * PCM_BYTES_PER_SAMPLE))
            for i in range(samples):
                t = (n + i) / self.sample_rate
                if t % 1.0 < BEEP_ON:
                    frame[i] = int(6000 * math.sin(2 * math.pi * 440 * t))
            n += samples
            pipe.push_from_phone(frame.tobytes())
            heard = array.array("h", pipe.pull_to_phone(samples * PCM_BYTES_PER_SAMPLE))
            rms = math.sqrt(sum(s * s for s in heard) / len(heard)) / 32768
            self.rx_level = 0.8 * self.rx_level + 0.2 * rms
            next_at += FRAME_MS / 1000
            await asyncio.sleep(max(0, next_at - loop.time()))
