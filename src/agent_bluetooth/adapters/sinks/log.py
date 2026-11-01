"""Prints every event as one JSON line. Pipe it anywhere."""

from __future__ import annotations

import json
from typing import Any

from ...core.context import Context
from ...core.events import Event


class LogSink:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.events: set[str] = set(cfg.get("events", []))  # empty = all

    async def handle(self, event: Event) -> None:
        if not self.events or event.type in self.events:
            print(json.dumps(event.to_dict(), ensure_ascii=False), flush=True)
