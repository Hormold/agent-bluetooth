"""What every adapter receives: the bus, its own config, and shared resources."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .tasks import Tasks

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .bus import EventBus

T = TypeVar("T")


@dataclass(frozen=True)
class ResourceKey(Generic[T]):
    """Names one shared resource and the type it must be, so `Context.resource`
    returns a checked `T`, not whatever happened to be stored under a string."""

    name: str
    type: type[T]


@dataclass
class Context:
    bus: EventBus
    config: dict[str, Any]  # whole config, for adapters that need a shared section
    tasks: Tasks = field(init=False)  # daemon-lifetime work; App.run closes it on shutdown
    _resources: dict[str, object] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.tasks = Tasks(self.bus.instance)

    async def resource(self, key: ResourceKey[T], factory: Callable[[], Awaitable[T]]) -> T:
        """One shared instance per key, e.g. a single Bluetooth radio used by
        both the phone and messages adapters."""
        async with self._lock:
            if key.name not in self._resources:
                self._resources[key.name] = await factory()
            return self._get(key)

    def sync_resource(self, key: ResourceKey[T], factory: Callable[[], T]) -> T:
        """Like `resource`, for cheap objects built without awaiting."""
        if key.name not in self._resources:
            self._resources[key.name] = factory()
        return self._get(key)

    def resources(self) -> list[object]:
        return list(self._resources.values())

    def _get(self, key: ResourceKey[T]) -> T:
        value = self._resources[key.name]
        if not isinstance(value, key.type):
            raise TypeError(f"resource {key.name!r} is a {type(value).__name__}, not {key.type}")
        return value
