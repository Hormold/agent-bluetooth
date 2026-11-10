"""Call recording policy: one tape per call, named after it, saved when it ends.
The tape itself (what gets written) is the AudioKit's; this decides when."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .events import E

if TYPE_CHECKING:
    from .bus import EventBus
    from .lines import Line
    from .ports import AudioKit, Recording

log = logging.getLogger(__name__)


def tape_for(line: Line, audio: AudioKit, record_dir: str, sample_rate: int) -> Recording:
    """One recording per call, kept across audio links (the line can drop
    and come back mid-call)."""
    if line.tape is not None:
        return line.tape
    call = line.call
    digits = "".join(c for c in call.number if c.isdigit()) or "unknown"
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{call.direction}-{digits}.wav"
    line.tape = audio.tape(Path(record_dir).expanduser() / name, sample_rate)
    log.info("recording call %s -> %s", call.id, line.tape.path)
    return line.tape


def save_tape(line: Line, bus: EventBus) -> str:
    """Close the call's tape, if any; returns the file it was saved to ("" if none)."""
    tape, line.tape = line.tape, None
    if tape is None:
        return ""
    tape.close()
    log.info("saved %s (%.1fs)", tape.path, tape.seconds)
    bus.publish(
        E.RECORDING_SAVED,
        call_id=line.call.id,
        number=line.call.number,
        file=str(tape.path),
        seconds=round(tape.seconds, 1),
    )
    return str(tape.path)
