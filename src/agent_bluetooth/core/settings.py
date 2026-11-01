"""Runtime settings the app (or an agent) edits once and that persist.

The single source of truth both the UI and agents read/write over /api/settings.
Saved to a JSON overlay so a one-time setup survives restarts; applied live to
the router and the LiveKit bridge without a reload.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)

FIELDS = ("incoming", "outgoing", "agent_name", "play_file", "answer")
LIVEKIT_FIELDS = ("url", "client_url", "api_key", "api_secret")


class SettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (OSError, ValueError):
                log.warning("could not read settings %s", self.path)
        return {}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))


def current(app: App) -> dict[str, Any]:
    r = app.router
    out: dict[str, Any] = {
        "incoming": r.incoming_mode,
        "outgoing": r.outgoing_mode,
        "agent_name": r.agent_name,
        "play_file": r.play_path,
        "answer": r.answer_mode,
    }
    b = app.bridge
    out["livekit"] = {f: getattr(b, f, "") for f in LIVEKIT_FIELDS}
    return out


def apply(app: App, data: dict[str, Any]) -> None:
    r = app.router
    if "incoming" in data:
        r.incoming_mode = str(data["incoming"])
    if "outgoing" in data:
        r.outgoing_mode = str(data["outgoing"])
    if "agent_name" in data:
        r.agent_name = str(data["agent_name"])
    if "play_file" in data:
        r.play_path = str(data["play_file"])
    if "answer" in data:
        r.answer_mode = str(data["answer"])
    lk = data.get("livekit") or {}
    for f in LIVEKIT_FIELDS:
        if f in lk and lk[f]:
            setattr(app.bridge, f, str(lk[f]))
