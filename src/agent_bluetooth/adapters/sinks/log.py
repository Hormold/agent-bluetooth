"""Prints every event as one JSON line. Pipe it anywhere."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from ...core.bus import QUIET_EVENTS

if TYPE_CHECKING:
    from ...core.context import Context
    from ...core.events import Event


class LogSink:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.events: set[str] = set(cfg.get("events", []))  # empty = all but telemetry

    async def handle(self, event: Event) -> None:
        if event.type in self.events or (not self.events and event.type not in QUIET_EVENTS):
            sys.stdout.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            sys.stdout.flush()
