"""Wiring: config -> adapters -> router -> HTTP API. Nothing else lives here."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from . import registry
from .api.http import HttpApi
from .config import load_config
from .core.actions import build_actions
from .core.automations import AutomationEngine
from .core.bus import EventBus
from .core.context import Context
from .core.router import CallRouter

log = logging.getLogger("agent_bluetooth")


class App:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.ctx = Context(bus=EventBus(), config=config)
        self.phone = registry.build("phone", config["phone"], self.ctx)
        msg_cfg = config.get("messages", {"adapter": "none"})
        self.messages = (
            None if msg_cfg.get("adapter") == "none"
            else registry.build("messages", msg_cfg, self.ctx)
        )
        self.bridge = registry.build("bridge", config["bridge"], self.ctx)
        self.sinks = [registry.build("sink", s, self.ctx) for s in config.get("sinks", [])]
        for sink in self.sinks:
            self.ctx.bus.subscribe(sink.handle)
        self.router = CallRouter(self.ctx, self.phone, self.bridge, config.get("routing", {}))
        self.automations = AutomationEngine(self.ctx, build_actions(self))
        self.api = HttpApi(self, config.get("http", {}))

    def components(self) -> list[Any]:
        return [c for c in (self.bridge, self.phone, self.messages) if c is not None]

    async def run(self) -> None:
        for c in self.components():
            await c.start()
        await self.api.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        log.info("shutting down")
        await self.api.stop()
        for c in reversed(self.components()):
            await c.stop()
        for resource in self.ctx.resources():
            if close := getattr(resource, "close", None):
                await close()


def run_config(config_path: str, verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
    )
    asyncio.run(App(load_config(config_path)).run())


if __name__ == "__main__":
    import sys
    run_config(sys.argv[1] if len(sys.argv) > 1 else "config.toml", "-v" in sys.argv)
