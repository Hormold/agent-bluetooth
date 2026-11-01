"""What every adapter receives: the bus, its own config, and shared resources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .bus import EventBus


@dataclass
class Context:
    bus: EventBus
    config: dict[str, Any]  # whole config, for adapters that need a shared section
    _resources: dict[str, Any] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def resource(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        """One shared instance per key, e.g. a single Bluetooth radio used by
        both the phone and messages adapters."""
        async with self._lock:
            if key not in self._resources:
                self._resources[key] = await factory()
            return self._resources[key]

    def resources(self) -> list[Any]:
        return list(self._resources.values())
