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
from .core.settings import SettingsStore
from .core.settings import apply as settings_apply
from .core.watchdog import heartbeat, sd_notify

log = logging.getLogger("agent_bluetooth")


class App:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.ctx = Context(bus=EventBus(), config=config)
        self.phone = registry.build("phone", config["phone"], self.ctx)
        msg_cfg = config.get("messages", {"adapter": "none"})
        self.messages = (
            None
            if msg_cfg.get("adapter") == "none"
            else registry.build("messages", msg_cfg, self.ctx)
        )
        dir_cfg = config.get("directory", {"adapter": "none"})
        self.directory = (
            None
            if dir_cfg.get("adapter", "none") == "none"
            else registry.build("directory", dir_cfg, self.ctx)
        )
        self.bridge = registry.build("bridge", config["bridge"], self.ctx)
        self.sinks = [
            registry.build("sink", s, self.ctx) for s in config.get("sinks", [])
        ]
        for sink in self.sinks:
            self.ctx.bus.subscribe(sink.handle)
        self.router = CallRouter(
            self.ctx, self.phone, self.bridge, config.get("routing", {}), self.directory
        )
        self.settings = SettingsStore(
            config.get("settings_file", "~/.agent-bluetooth/settings.json")
        )
        saved = self.settings.load()
        if saved:
            settings_apply(self, saved)
        self.automations = AutomationEngine(self.ctx, build_actions(self))
        self.api = HttpApi(self, config.get("http", {}))

    def components(self) -> list[Any]:
        return [
            c
            for c in (self.bridge, self.phone, self.messages, self.directory)
            if c is not None
        ]

    async def run(self) -> None:
        # Liveness is decoupled from the radio: the API + watchdog come up first
        # and we tell systemd we're READY immediately, THEN bring the radio up in
        # the background (retrying through a wedged dongle). A flaky radio can
        # never make the daemon fail to start or wedge — it just shows "phone
        # offline" until it recovers on its own.
        await self.api.start()
        hb = asyncio.create_task(heartbeat())
        starts = [asyncio.create_task(self._supervise(c)) for c in self.components()]
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        log.info("shutting down")
        sd_notify("STOPPING=1")
        hb.cancel()
        for t in starts:
            t.cancel()
        # Time-box shutdown: a stuck USB close must not turn into a SIGKILL that
        # leaves the dongle wedged for the next start.
        try:
            await asyncio.wait_for(self._shutdown(), timeout=6)
        except TimeoutError:
            log.warning("shutdown timed out; exiting anyway")

    async def _supervise(self, component: Any) -> None:
        try:
            await component.start()
        except Exception:
            log.exception("component %s failed to start", type(component).__name__)

    async def _shutdown(self) -> None:
        await self.api.stop()
        for c in reversed(self.components()):
            try:
                await c.stop()
            except Exception:
                log.debug("stop of %s failed", type(c).__name__, exc_info=True)
        for resource in self.ctx.resources():
            if close := getattr(resource, "close", None):
                try:
                    await close()
                except Exception:
                    log.debug("resource close failed", exc_info=True)


def run_config(config_path: str, verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
    )
    # Bumble logs every HCI/SCO packet at DEBUG. With a live SCO (voice) link
    # that's thousands of lines/sec — it pegs the CPU and journald on a small
    # SBC and starves the event loop, dropping the very call it's logging. Keep
    # these at INFO even under -v; our own loggers still honour verbose.
    for noisy in (
        "bumble.host",
        "bumble.transport.usb",
        "bumble.hci",
        "bumble.rfcomm",
        "bumble.l2cap",
    ):
        logging.getLogger(noisy).setLevel(logging.INFO)
    asyncio.run(App(load_config(config_path)).run())


if __name__ == "__main__":
    import sys

    run_config(sys.argv[1] if len(sys.argv) > 1 else "config.toml", "-v" in sys.argv)
