"""POSTs events as JSON to a URL (n8n, Zapier, your agent, ...)."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ...core.context import Context
from ...core.events import Event

log = logging.getLogger(__name__)


class WebhookSink:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.url: str = cfg["url"]
        self.events: set[str] = set(cfg.get("events", []))  # empty = all
        self.headers: dict[str, str] = cfg.get("headers", {})

    async def handle(self, event: Event) -> None:
        if self.events and event.type not in self.events:
            return
        async with aiohttp.ClientSession() as http:
            async with http.post(
                self.url, json=event.to_dict(), headers=self.headers
            ) as r:
                if r.status >= 400:
                    log.warning(
                        "webhook %s -> %s %s", self.url, r.status, await r.text()
                    )
