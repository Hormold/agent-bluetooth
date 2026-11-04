"""Tiny in-process pub/sub. Handlers never block each other."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .events import Event
from .tasks import Tasks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = logging.getLogger(__name__)
Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self, instance: str = "default", history: int = 200, backlog: int = 500) -> None:
        self.instance = instance
        self.backlog = backlog
        self._handlers: list[Handler] = []
        self._queues: set[asyncio.Queue[Event]] = set()
        self.history: deque[Event] = deque(maxlen=history)
        self.tasks = Tasks(f"{instance}:bus")  # handler dispatch; App.run closes it last

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def publish(self, type_: str, **data: object) -> Event:
        event = Event(type_, data, instance=self.instance)
        self.history.append(event)
        log.info("event %s %s", type_, data)
        for handler in self._handlers:
            self.tasks.spawn(self._safe(handler, event), name=f"bus:{type_}")
        for queue in self._queues:
            if queue.full():  # a stalled client loses its oldest events, not our memory
                queue.get_nowait()
            queue.put_nowait(event)
        return event

    async def stream(self) -> AsyncIterator[Event]:
        """Live feed for SSE clients."""
        queue: asyncio.Queue[Event] = asyncio.Queue(self.backlog)
        self._queues.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues.discard(queue)

    @staticmethod
    async def _safe(handler: Handler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            log.exception("handler %s failed on %s", handler, event.type)
