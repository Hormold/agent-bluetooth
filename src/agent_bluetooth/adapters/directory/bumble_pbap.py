"""Directory adapter: contacts + call history from the phone over PBAP.

Fetched on demand (the API calls contacts()/history()), cached briefly so the
dialer stays snappy and we don't hammer the phone."""

from __future__ import annotations

import logging
import time
from typing import Any

from ...core.context import Context
from ..bumble.pbap import BumblePbap
from ..bumble.radio import BumbleRadio

log = logging.getLogger(__name__)


class BumblePbapDirectory:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.ctx = ctx
        self.ttl = float(cfg.get("cache_seconds", 60))
        self.pbap: BumblePbap | None = None
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    async def start(self) -> None:
        radio = await BumbleRadio.shared(self.ctx)
        self.pbap = BumblePbap(radio)

    async def stop(self) -> None:
        pass

    def _peer(self) -> str | None:
        if self.pbap is None:
            return None
        for c in self.pbap.radio.device.connections.values():
            return str(c.peer_address)
        return None

    async def _cached(self, key: str, fetch) -> list[dict]:
        now = time.time()
        if (hit := self._cache.get(key)) and now - hit[0] < self.ttl:
            return hit[1]
        address = self._peer()
        if address is None or self.pbap is None:
            raise RuntimeError("phone not connected")
        items = [x.to_dict() for x in await fetch(address)]
        self._cache[key] = (now, items)
        return items

    async def contacts(self) -> list[dict]:
        return await self._cached("contacts", lambda a: self.pbap.contacts(a))

    async def history(self) -> list[dict]:
        return await self._cached("history", lambda a: self.pbap.history(a))
