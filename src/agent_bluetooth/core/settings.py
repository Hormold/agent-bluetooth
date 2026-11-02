"""Runtime settings the app (or an agent) edits once and that persist.

The single source of truth both the UI and agents read/write over /api/settings.
Saved to a JSON overlay so a one-time setup survives restarts; applied live to
the router and the LiveKit bridge without a reload.
"""

from __future__ import annotations

import json
import logging
import wave
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
    out["rules"] = r.rules
    descriptions = []
    for label, direction, mode in (
        ("Incoming", "in", r.incoming_mode),
        ("Outgoing", "out", r.outgoing_mode),
    ):
        descriptions.append(f"{label} calls: {_describe(r, mode, direction)}.")
        for rule in r.rules:
            if rule.get("direction", "in") in (direction, "any"):
                action = rule.get("mode", rule.get("action", "app"))
                descriptions.append(
                    f"{label} from {_who(rule)}: {_describe(r, action, direction, rule)}."
                )
    if r.rules:
        descriptions.append("The first matching rule overrides the direction default.")
    if r.answer_mode == "manual":
        descriptions.append(
            "answer=manual currently answers app/agent calls immediately; use incoming=ignore for manual control."
        )
    bridge = app.config.get("bridge", {}).get("adapter", "livekit")
    if bridge != "livekit":
        descriptions.append(
            f"Bridge adapter is {bridge!r}; LiveKit room behavior requires the livekit adapter."
        )
    out["summary"] = " ".join(descriptions)
    return out


def _who(rule: dict[str, Any]) -> str:
    parts = []
    if "when_contact" in rule:
        parts.append("contacts" if rule["when_contact"] else "numbers not in contacts")
    if rule.get("when_number"):
        parts.append(f"numbers matching {rule['when_number']}")
    return " and ".join(parts) or "any number"


def _describe(r, mode: str, direction: str, rule: dict[str, Any] | None = None) -> str:
    rule = rule or {}
    play_path = rule.get("play_file") or r.play_path
    agent_name = rule.get("agent_name") or r.agent_name
    incoming = direction == "in"
    if mode == "ignore":
        return "left for manual control"
    if mode == "reject" and incoming:
        return "rejected without answering"
    if mode == "play" and play_path:
        start = "answered automatically and " if incoming else "when connected, "
        return f"{start}{Path(play_path).name} is played, then hung up"
    if mode == "record":
        start = "answered automatically and " if incoming else "when connected, "
        return f"{start}audio is recorded to {r.record_dir}"
    if mode in ("app", "agent") or (mode == "play" and not play_path):
        if not incoming:
            answer = "when connected, audio goes to a LiveKit room"
        elif r.answer_mode == "on_join":
            answer = "a LiveKit room opens; answered when a participant joins"
        else:
            answer = "answered automatically; audio goes to a LiveKit room"
        if mode == "agent":
            answer += (
                f"; dispatch agent {agent_name!r}"
                if agent_name
                else "; no agent_name configured, so no agent is dispatched"
            )
        if mode == "play":
            answer += "; no play_file configured, so room fallback is used"
        return answer
    return f"no automatic handling for mode {mode!r}; use app, agent, play, record or ignore"


def validate_wav(path: str) -> None:
    """Validate on the daemon, where playback will read the file."""
    if not path or not Path(path).is_absolute():
        raise ValueError("play_file must be an absolute path on the daemon's machine")
    try:
        with wave.open(path, "rb") as wav:
            if wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise ValueError("play_file must be an uncompressed 16-bit PCM WAV")
            if wav.getnchannels() not in (1, 2):
                raise ValueError("play_file must be a mono or stereo 16-bit PCM WAV")
            if not wav.getnframes() or len(wav.readframes(1)) != 2 * wav.getnchannels():
                raise ValueError("play_file contains no readable audio frames")
    except (OSError, wave.Error, EOFError) as exc:
        raise ValueError(
            f"Cannot read play_file {path!r}: use an existing, readable 16-bit PCM WAV"
        ) from exc


def validate(app: App, data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object")
    unknown = set(data) - {*FIELDS, "livekit", "rules"}
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    for field in FIELDS:
        if field in data and not isinstance(data[field], str):
            raise ValueError(f"{field} must be a string")
    modes = {"app", "agent", "play", "record", "ignore"}
    for field in ("incoming", "outgoing"):
        allowed = modes | ({"reject"} if field == "incoming" else set())
        if field in data and data[field] not in allowed:
            raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    if "answer" in data and data["answer"] not in {"auto", "on_join", "manual"}:
        raise ValueError(
            "answer must be auto, on_join or manual; use incoming=ignore for manual call control"
        )
    if "livekit" in data:
        lk = data["livekit"]
        if not isinstance(lk, dict):
            raise ValueError("livekit must be a JSON object")
        bad = set(lk) - set(LIVEKIT_FIELDS)
        if bad:
            raise ValueError(
                f"Unknown livekit settings: {', '.join(sorted(bad))} (use {', '.join(LIVEKIT_FIELDS)})"
            )
        for k, v in lk.items():
            if not isinstance(v, str):
                raise ValueError(f"livekit.{k} must be a string")
        for k in ("url", "client_url"):
            v = lk.get(k)
            if v and not v.startswith(("ws://", "wss://", "http://", "https://")):
                raise ValueError(
                    f"livekit.{k} must start with ws://, wss://, http:// or https://"
                )
    if "rules" in data:
        validate_rules(data["rules"], data.get("play_file") or app.router.play_path)
    merged = {**current(app), **data}
    if (
        merged["incoming"] == "play"
        or merged["outgoing"] == "play"
        or data.get("play_file")
    ):
        validate_wav(merged["play_file"])


RULE_KEYS = {
    "direction",
    "when_number",
    "when_contact",
    "mode",
    "action",
    "agent_name",
    "play_file",
}
RULE_MODES = {"app", "agent", "play", "record", "reject", "ignore"}


def validate_rules(rules: Any, default_play: str = "") -> None:
    """Rules route a call by who calls. First match wins. Each rule:
    {direction: in|out|any, when_number: glob, when_contact: bool,
     mode: app|agent|play|record|reject|ignore, agent_name?, play_file?}"""
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")
    for i, rule in enumerate(rules):
        where = f"rules[{i}]"
        if not isinstance(rule, dict):
            raise ValueError(f"{where} must be an object")
        bad = set(rule) - RULE_KEYS
        if bad:
            raise ValueError(f"{where}: unknown keys {', '.join(sorted(bad))}")
        mode = rule.get("mode", rule.get("action"))
        if mode not in RULE_MODES:
            raise ValueError(
                f"{where}: mode must be one of {', '.join(sorted(RULE_MODES))}"
            )
        if rule.get("direction", "in") not in {"in", "out", "any"}:
            raise ValueError(f"{where}: direction must be in, out or any")
        if "when_contact" in rule and not isinstance(rule["when_contact"], bool):
            raise ValueError(f"{where}: when_contact must be true or false")
        for k in ("when_number", "agent_name", "play_file"):
            if k in rule and not isinstance(rule[k], str):
                raise ValueError(f"{where}: {k} must be a string")
        if mode == "play":
            validate_wav(rule.get("play_file") or default_play)


def apply(app: App, data: dict[str, Any]) -> None:
    r = app.router
    if "rules" in data:
        r.rules = list(data["rules"])
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
    lk = dict(data.get("livekit") or {})
    bridge = app.bridge
    # client_url (what apps connect to) usually just mirrors url. If it did,
    # move it along with url; keep it only if it was set to something else.
    if (
        lk.get("url")
        and "client_url" not in lk
        and getattr(bridge, "client_url", None) == getattr(bridge, "url", None)
    ):
        lk["client_url"] = lk["url"]
    for f in LIVEKIT_FIELDS:
        if f in lk and lk[f]:
            setattr(bridge, f, str(lk[f]))
