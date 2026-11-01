"""Tiny in-process pub/sub. Handlers never block each other."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable

from .events import Event

log = logging.getLogger(__name__)
Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._handlers: list[Handler] = []
        self._queues: set[asyncio.Queue[Event]] = set()
        self.history: deque[Event] = deque(maxlen=history)

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def publish(self, type_: str, **data) -> Event:
        event = Event(type_, data)
        self.history.append(event)
        log.info("event %s %s", type_, data)
        for handler in self._handlers:
            asyncio.create_task(self._safe(handler, event))
        for queue in self._queues:
            queue.put_nowait(event)
        return event

    async def stream(self) -> AsyncIterator[Event]:
        """Live feed for SSE clients."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
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
