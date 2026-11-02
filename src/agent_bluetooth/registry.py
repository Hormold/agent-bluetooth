"""Adapter registry. Add a new adapter = one line here, or point the config at
your own class directly: adapter = "my_package.my_module:MyPhone"."""

from __future__ import annotations

import importlib
from typing import Any

from .core.context import Context

_P = "agent_bluetooth.adapters"
ADAPTERS: dict[str, dict[str, str]] = {
    "phone": {
        "fake": f"{_P}.phone.fake:FakePhone",
        "bumble-hfp": f"{_P}.phone.bumble_hfp:BumbleHfpPhone",
    },
    "messages": {
        "fake": f"{_P}.messages.fake:FakeMessages",
        "bumble-map": f"{_P}.messages.bumble_map:BumbleMapMessages",
    },
    "directory": {
        "bumble-pbap": f"{_P}.directory.bumble_pbap:BumblePbapDirectory",
    },
    "bridge": {
        "livekit": f"{_P}.bridge.livekit_room:LiveKitBridge",
        "null": f"{_P}.bridge.null:NullBridge",
        "record": f"{_P}.bridge.recorder:RecorderBridge",
    },
    "sink": {
        "log": f"{_P}.sinks.log:LogSink",
        "webhook": f"{_P}.sinks.webhook:WebhookSink",
        "telegram": f"{_P}.sinks.telegram:TelegramSink",
    },
}


def build(kind: str, cfg: dict[str, Any], ctx: Context) -> Any:
    name = cfg.get("adapter", "")
    target = ADAPTERS[kind].get(name, name)
    if ":" not in target:
        known = ", ".join(ADAPTERS[kind])
        raise ValueError(
            f"unknown {kind} adapter {name!r} (known: {known}, or module:Class)"
        )
    module, cls = target.split(":", 1)
    return getattr(importlib.import_module(module), cls)(ctx, cfg)
