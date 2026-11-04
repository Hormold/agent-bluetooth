"""The router's per-call state: one `Line` per live call, created on the first
phone event that carries the call and retired when it ends."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .ports import Call

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .ports import AudioHandler, Recording

ENDED_REMEMBERED = 64  # late events for these call ids must not bring the call back
# Filled in by the router (contacts, agent dials); phone events send them empty.
_ROUTER_KNOWS = ("number", "name", "contact", "room", "task")


@dataclass(eq=False)
class Line:
    """One live call. `lock` serializes every transition (routing, play,
    bridge, clip done, end) so they never interleave."""

    call: Call
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    handler: AudioHandler | None = None
    tag: str = ""  # names the current handler; completions from older ones are stale
    routing: asyncio.Task[None] | None = None  # automatic routing (rules / modes)
    empty_check: asyncio.Task[None] | None = None
    tape: Recording | None = None
    ended: bool = False
    released: asyncio.Event = field(default_factory=asyncio.Event)  # end() finished undoing it


class LineBook:
    """Live lines by call id, plus the ids that just ended."""

    def __init__(self) -> None:
        self._lines: dict[str, Line] = {}
        self._ended: deque[str] = deque(maxlen=ENDED_REMEMBERED)
        self.ending: set[Line] = set()  # retired, still releasing what they held

    def __iter__(self) -> Iterator[Line]:
        return iter(list(self._lines.values()))

    def get(self, call_id: str) -> Line | None:
        return self._lines.get(call_id)

    def calls(self) -> dict[str, Call]:
        return {cid: line.call for cid, line in self._lines.items()}

    def track(self, data: dict[str, Any]) -> Line | None:
        """The live call for a phone event, created on first sight; None for a
        call that already ended (a late event must not bring it back)."""
        line = self._lines.get(data["id"])
        if line is None:
            if data["id"] in self._ended or data.get("state") == "ended":
                return None
            line = self._lines[data["id"]] = Line(Call(**data))
            return line
        for k, v in data.items():
            if k in _ROUTER_KNOWS and not v:
                continue  # the phone doesn't know it; keep what we found
            setattr(line.call, k, v)
        return line

    def retire(self, line: Line) -> None:
        """Mark the call over; it stays in `ending` until its release is done."""
        line.ended = True
        line.call.state = "ended"
        self._lines.pop(line.call.id, None)
        self._ended.append(line.call.id)
        self.ending.add(line)

    def released(self, line: Line) -> None:
        self.ending.discard(line)
        line.released.set()
