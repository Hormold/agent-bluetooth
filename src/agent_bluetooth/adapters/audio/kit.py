"""The AudioKit port on local WAV files: clip playback, stall phrases, call
recording. The router asks for handlers through this; it never builds them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .file_player import FilePlayer
from .stall import StallPlayer
from .tape import CallTape

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.bus import EventBus


class WavKit:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    def player(
        self, path: str, *, tag: str, loop: bool = False, hangup_after: bool = True
    ) -> FilePlayer:
        return FilePlayer(self.bus, path, tag=tag, loop=loop, hangup_after=hangup_after)

    def stall(self, folder: str, max_minutes: float, *, tag: str) -> StallPlayer:
        return StallPlayer(self.bus, folder, max_minutes, tag=tag)

    @staticmethod
    def tape(path: Path, sample_rate: int) -> CallTape:
        return CallTape(path, sample_rate)
