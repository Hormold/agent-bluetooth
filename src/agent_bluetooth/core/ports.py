"""Ports: the contracts. Swap any adapter without touching the rest.

    Phone    -> talks to the handset (calls + call audio).   e.g. bumble-hfp, fake
    Messages -> SMS in/out.                                   e.g. bumble-map, fake
    Bridge   -> where call audio goes.                        e.g. livekit
    Sink     -> where notifications go.                       e.g. telegram, webhook, log

Adapters only talk to each other through the EventBus and AudioPipe.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from .events import Event

PCM_BYTES_PER_SAMPLE = 2  # all audio is 16-bit signed little-endian mono


@dataclass
class Call:
    direction: str  # "in" | "out"
    number: str = ""
    name: str = ""
    state: str = "ringing"  # ringing | dialing | active | ended
    contact: bool = False  # number is in the phone's contacts (name filled from there)
    room: str = ""  # LiveKit room to join instead of a new one (agent-placed calls)
    task: str = ""  # what an agent placing this call should get done
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AudioPipe:
    """Two-way PCM pipe between a Phone adapter and a Bridge adapter.

    The phone side owns the clock: it pushes what it hears and pulls exactly as
    many bytes as it needs to send (silence if the bridge is quiet). The bridge
    side just reads and writes. Buffers are bounded so latency never grows.
    """

    def __init__(
        self, sample_rate: int, max_buffer_ms: int = 400, tx_prime_ms: int = 140
    ) -> None:
        self.sample_rate = sample_rate
        self._max_bytes = sample_rate * PCM_BYTES_PER_SAMPLE * max_buffer_ms // 1000
        # The phone pulls outgoing audio in USB-sized bursts (~128 ms at once)
        # while bridges write in real time. Without a cushion every burst finds
        # 20-40 ms buffered and the rest is padded with silence (choppy audio to
        # the caller). So after running dry, wait until this much is queued.
        self._tx_prime = sample_rate * PCM_BYTES_PER_SAMPLE * tx_prime_ms // 1000
        self._tx_primed = False
        self._closed = False
        self._from_phone: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=50)
        self._to_phone = bytearray()
        self.dropped = 0  # chunks from the phone dropped because the reader fell behind
        self.pushed = 0  # bytes received from the phone
        self.opened_at = time.monotonic()
        self.bridge_buffer_ms = 0.0  # set by the bridge: audio queued on its side
        self.tap: AudioTap | None = None  # call recording, when on

    # phone side ------------------------------------------------------------
    def push_from_phone(self, pcm: bytes) -> None:
        self.pushed += len(pcm)
        if self.tap:
            self.tap.heard(pcm)
        if self._from_phone.full():
            self._from_phone.get_nowait()  # drop oldest, stay real-time
            self.dropped += 1
        self._from_phone.put_nowait(pcm)

    def pull_to_phone(self, nbytes: int) -> bytes:
        out = self._next_to_phone(nbytes)
        if self.tap:
            self.tap.sent(out)
        return out

    def _next_to_phone(self, nbytes: int) -> bytes:
        if not self._tx_primed:
            if len(self._to_phone) < self._tx_prime:
                return b"\x00" * nbytes  # still filling the cushion
            self._tx_primed = True
        if len(self._to_phone) < nbytes:
            self._tx_primed = False  # ran dry: rebuild the cushion before resuming
        chunk = bytes(self._to_phone[:nbytes])
        del self._to_phone[:nbytes]
        return chunk + b"\x00" * (nbytes - len(chunk))

    def live(self) -> dict[str, Any]:
        """Numbers for the call screen's stats panel."""
        got = self.pushed / PCM_BYTES_PER_SAMPLE / self.sample_rate
        wall = time.monotonic() - self.opened_at
        return {
            "sample_rate": self.sample_rate,
            "seconds": round(wall, 1),
            "delivered_pct": round(100 * got / max(wall, 0.01), 1),
            "dropped": self.dropped,
            "to_phone_ms": round(
                len(self._to_phone) / PCM_BYTES_PER_SAMPLE / self.sample_rate * 1000
            ),
            "bridge_buffer_ms": round(self.bridge_buffer_ms),
            "box_load": round(os.getloadavg()[0], 2),
        }

    def stats(self) -> str:
        """Audio actually delivered by the phone vs wall time since the link opened."""
        got = self.pushed / PCM_BYTES_PER_SAMPLE / self.sample_rate
        wall = time.monotonic() - self.opened_at
        return f"phone delivered {got:.1f}s of audio in {wall:.1f}s ({100 * got / max(wall, 0.01):.0f}%), dropped {self.dropped} chunks"

    def close(self) -> None:
        """Signal end-of-audio. Safe to call twice and when nobody reads the
        caller side (play mode leaves the queue full)."""
        if self._closed:
            return
        self._closed = True
        if self._from_phone.full():
            self._from_phone.get_nowait()
        self._from_phone.put_nowait(None)

    # bridge side -----------------------------------------------------------
    async def read(self) -> bytes | None:
        """Next chunk heard on the phone line. None = pipe closed."""
        return await self._from_phone.get()

    def read_nowait(self) -> bytes | None:
        """Next buffered chunk without waiting; b"" if none, None if closed."""
        if self._from_phone.empty():
            return b""
        return self._from_phone.get_nowait()

    def read_discard(self) -> None:
        """Drop buffered caller audio (used while playing a clip, to avoid echo)."""
        while not self._from_phone.empty():
            self._from_phone.get_nowait()

    def write(self, pcm: bytes) -> None:
        self._to_phone += pcm
        overflow = len(self._to_phone) - self._max_bytes
        if overflow > 0:
            del self._to_phone[:overflow]


class AudioTap(Protocol):
    """Sees the line audio both ways, on the phone's clock (call recording)."""

    def heard(self, pcm: bytes) -> None: ...
    def sent(self, pcm: bytes) -> None: ...


@runtime_checkable
class Component(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@runtime_checkable
class Phone(Component, Protocol):
    def status(self) -> dict[str, Any]: ...
    def audio(self) -> AudioPipe | None:
        """Pipe for the live call audio, or None when no audio link is up."""

    async def answer(self) -> None: ...
    async def hangup(self) -> None: ...
    async def dial(self, number: str) -> None: ...
    async def send_dtmf(self, digits: str) -> None: ...
    async def set_volume(self, level: int) -> None:
        """Speaker volume 0..15 (HFP scale)."""


@runtime_checkable
class Messages(Component, Protocol):
    async def send(self, to: str, text: str) -> None: ...


@runtime_checkable
class AudioHandler(Protocol):
    """Something that consumes/produces a live call's audio: a LiveKit room, a
    WAV player, stall phrases. The router attaches exactly one per call."""

    async def attach_audio(self, call: Call, audio: AudioPipe) -> None: ...
    async def close(self, call: Call) -> None: ...


@runtime_checkable
class Bridge(AudioHandler, Protocol):
    async def open(self, call: Call) -> dict[str, Any]:
        """Create the room for a call (before it is answered), or join call.room
        when set. Returns room info.
        Must publish ROOM_JOINED / ROOM_LEFT with call_id and `others` count."""

    def join_info(self, room: str, identity: str) -> dict[str, Any]:
        """Credentials for a human/agent to join a room."""


@runtime_checkable
class Sink(Protocol):
    async def handle(self, event: Event) -> None: ...


@runtime_checkable
class Simulator(Protocol):
    """Optional: adapters that can fake inbound traffic (dev/demo)."""

    async def simulate(self, kind: str, **params: Any) -> None: ...
