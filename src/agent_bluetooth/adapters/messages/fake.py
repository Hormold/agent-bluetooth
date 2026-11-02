"""Pretend SMS: inject inbound messages from the API, log outbound ones."""

from __future__ import annotations

from typing import Any

from ...core.context import Context
from ...core.events import E


class FakeMessages:
    simulates = {"sms"}

    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.bus = ctx.bus

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, to: str, text: str) -> None:
        self.bus.publish(E.SMS_SENT, to=to, text=text)

    async def simulate(
        self, kind: str, sender: str = "+15551234567", text: str = "Hello!", **_: Any
    ) -> None:
        self.bus.publish(E.SMS_RECEIVED, sender=sender, text=text, id="fake")
