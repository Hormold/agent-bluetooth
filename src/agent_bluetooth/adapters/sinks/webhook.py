"""POSTs events as JSON to a URL (n8n, Zapier, your agent, ...)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiohttp

from ._http import DEFAULT_TIMEOUT_S, http_url, post_json

if TYPE_CHECKING:
    from ...core.context import Context
    from ...core.events import Event


class WebhookSink:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.url: str = http_url(cfg["url"], "webhook sink")
        self.events: set[str] = set(cfg.get("events", []))  # empty = all
        self.headers: dict[str, str] = cfg.get("headers", {})
        self.limit = aiohttp.ClientTimeout(total=float(cfg.get("timeout", DEFAULT_TIMEOUT_S)))

    async def handle(self, event: Event) -> None:
        if self.events and event.type not in self.events:
            return
        await post_json(
            self.url,
            event.to_dict(),
            what=f"webhook {self.url}",
            headers=self.headers,
            limit=self.limit,
        )
