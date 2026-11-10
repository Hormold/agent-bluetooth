"""Runtime settings the app (or an agent) edits once and that persist.

The single source of truth both the UI and agents read/write over /api/settings.
Saved to a JSON overlay so a one-time setup survives restarts; applied live to
the router and the LiveKit bridge without a reload.
"""

from __future__ import annotations

import json
import logging
import os
import re
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ports import PCM_BYTES_PER_SAMPLE

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .ports import SettingsHost
    from .router import CallRouter

log = logging.getLogger(__name__)

FIELDS = ("incoming", "outgoing", "agent_name", "play_file", "stall_dir", "answer")
RECORD_FIELDS = ("record_calls", "record_dir")  # global: every call, any mode
LIVEKIT_FIELDS = ("url", "client_url", "api_key", "api_secret")
MODES = {"app", "agent", "play", "ignore", "stall"}  # "reject" is incoming-only
ANSWER_MODES = {"auto", "on_join", "manual"}
URL_SCHEMES = ("ws://", "wss://", "http://", "https://")
WAV_CHANNELS = (1, 2)  # mono or stereo


class SettingsTypeError(TypeError, ValueError):
    """A setting has the wrong JSON type. Still a ValueError, so the API answers
    400 like every other invalid setting."""


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


def current(app: SettingsHost) -> dict[str, Any]:
    r = app.router
    out: dict[str, Any] = {
        "incoming": r.incoming_mode,
        "outgoing": r.outgoing_mode,
        "agent_name": r.agent_name,
        "play_file": r.play_path,
        "stall_dir": r.stall_dir,
        "answer": r.answer_mode,
        "record_calls": r.record_calls,
        "record_dir": r.record_dir,
    }
    b = app.bridge
    out["livekit"] = {f: getattr(b, f, "") for f in LIVEKIT_FIELDS}
    out["rules"] = r.rules
    out["automations"] = app.automations.runtime
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
    if r.record_calls:
        descriptions.append(
            f"Every call is recorded to {r.record_dir} (left: the other side, right: this side)."
        )
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


def _describe(r: CallRouter, mode: str, direction: str, rule: dict[str, Any] | None = None) -> str:
    rule = rule or {}
    play_path = rule.get("play_file") or r.play_path
    incoming = direction == "in"
    if mode == "stall":
        folder = rule.get("stall_dir") or r.stall_dir or "(no stall_dir set)"
        return (
            f"answered and kept busy with phrases from {folder} "
            f"for up to {r.stall_minutes:g} min, then hung up"
        )
    if mode == "ignore":
        return "left for manual control"
    if mode == "reject" and incoming:
        return "rejected without answering"
    if mode == "play" and play_path:
        start = "answered automatically and " if incoming else "when connected, "
        return f"{start}{Path(play_path).name} is played, then hung up"
    if mode in ("app", "agent", "play"):  # play without a file falls back to a room
        return _describe_room(r, mode, rule.get("agent_name") or r.agent_name, incoming=incoming)
    return f"no automatic handling for mode {mode!r}; use app, agent, play, record or ignore"


def _describe_room(r: CallRouter, mode: str, agent_name: str, *, incoming: bool) -> str:
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


def validate_wav(path: str) -> None:
    """Validate on the daemon, where playback will read the file."""
    if not path or not Path(path).is_absolute():
        raise ValueError("play_file must be an absolute path on the daemon's machine")
    try:
        with wave.open(path, "rb") as wav:
            if wav.getsampwidth() != PCM_BYTES_PER_SAMPLE or wav.getcomptype() != "NONE":
                raise ValueError("play_file must be an uncompressed 16-bit PCM WAV")
            channels = wav.getnchannels()
            if channels not in WAV_CHANNELS:
                raise ValueError("play_file must be a mono or stereo 16-bit PCM WAV")
            if not wav.getnframes() or len(wav.readframes(1)) != PCM_BYTES_PER_SAMPLE * channels:
                raise ValueError("play_file contains no readable audio frames")
    except (OSError, wave.Error, EOFError) as exc:
        raise ValueError(
            f"Cannot read play_file {path!r}: use an existing, readable 16-bit PCM WAV"
        ) from exc


def validate(app: SettingsHost, data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise SettingsTypeError("Settings must be a JSON object")
    unknown = set(data) - {*FIELDS, *RECORD_FIELDS, "livekit", "rules", "automations"}
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    _validate_types(data)
    if "record_dir" in data:
        validate_record_dir(data["record_dir"])
    _validate_modes(data)
    if "livekit" in data:
        _validate_livekit(data["livekit"])
    if "rules" in data:
        validate_rules(
            data["rules"],
            data.get("play_file") or app.router.play_path,
            data.get("stall_dir") or app.router.stall_dir,
        )
    if "automations" in data:
        validate_automations(app, data["automations"])
    _validate_media(app, data)


def _validate_types(data: dict[str, Any]) -> None:
    for field in FIELDS:
        if field in data and not isinstance(data[field], str):
            raise SettingsTypeError(f"{field} must be a string")
    if "record_calls" in data and not isinstance(data["record_calls"], bool):
        raise SettingsTypeError("record_calls must be true or false")


def _validate_modes(data: dict[str, Any]) -> None:
    for field in ("incoming", "outgoing"):
        allowed = MODES | ({"reject"} if field == "incoming" else set())
        if field in data and data[field] not in allowed:
            raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    if "answer" in data and data["answer"] not in ANSWER_MODES:
        raise ValueError(
            "answer must be auto, on_join or manual; use incoming=ignore for manual call control"
        )


def _validate_livekit(lk: object) -> None:
    if not isinstance(lk, dict):
        raise SettingsTypeError("livekit must be a JSON object")
    bad = set(lk) - set(LIVEKIT_FIELDS)
    if bad:
        raise ValueError(
            f"Unknown livekit settings: {', '.join(sorted(bad))} (use {', '.join(LIVEKIT_FIELDS)})"
        )
    for k, v in lk.items():
        if not isinstance(v, str):
            raise SettingsTypeError(f"livekit.{k} must be a string")
    for k in ("url", "client_url"):
        v = lk.get(k)
        if v and not v.startswith(URL_SCHEMES):
            raise ValueError(f"livekit.{k} must start with ws://, wss://, http:// or https://")


def _validate_media(app: SettingsHost, data: dict[str, Any]) -> None:
    """The files the resulting modes will play must exist and be readable now."""
    merged = {**current(app), **data}
    if merged["incoming"] == "play" or merged["outgoing"] == "play" or data.get("play_file"):
        validate_wav(merged["play_file"])
    if merged["incoming"] == "stall" or merged["outgoing"] == "stall":
        validate_stall_dir(merged.get("stall_dir", ""))


RULE_KEYS = {
    "direction",
    "when_number",
    "when_contact",
    "mode",
    "action",
    "agent_name",
    "play_file",
    "stall_dir",
}
RULE_MODES = {"app", "agent", "play", "reject", "ignore", "stall"}


def validate_record_dir(folder: object) -> None:
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError("record_dir must be a folder path")
    path = Path(folder).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Cannot create record_dir {folder!r}: {exc.strerror}") from exc
    if not os.access(path, os.W_OK):
        raise ValueError(f"record_dir {folder!r} is not writable")


def phrases_in(folder: str) -> list[Path]:
    """What a stall_dir holds: its .wav files, in a stable order. The stall
    player reads exactly this, so validation and playback agree."""
    return sorted(p for p in Path(folder).expanduser().glob("*.wav") if p.is_file())


def validate_stall_dir(folder: str) -> None:
    if not folder:
        raise ValueError("stall mode needs stall_dir: a folder of 16-bit WAV phrases")
    if not Path(folder).expanduser().is_dir():
        raise ValueError(f"stall_dir {folder!r} is not a folder")
    clips = phrases_in(folder)
    if not clips:
        raise ValueError(f"stall_dir {folder!r} has no .wav files")
    for clip in clips:
        validate_wav(str(clip))


def validate_rules(rules: object, default_play: str = "", default_stall: str = "") -> None:
    """Rules route a call by who calls. First match wins. Each rule:
    {direction: in|out|any, when_number: glob, when_contact: bool,
     mode: app|agent|play|reject|ignore|stall, agent_name?, play_file?, stall_dir?}"""
    if not isinstance(rules, list):
        raise SettingsTypeError("rules must be a list")
    for i, rule in enumerate(rules):
        where = f"rules[{i}]"
        if not isinstance(rule, dict):
            raise SettingsTypeError(f"{where} must be an object")
        mode = _validate_rule_shape(where, rule)
        if mode == "play":
            validate_wav(rule.get("play_file") or default_play)
        if mode == "stall":
            validate_stall_dir(rule.get("stall_dir") or default_stall)


def _validate_rule_shape(where: str, rule: dict[str, Any]) -> str:
    """Keys, mode, direction and value types of one rule; returns its mode."""
    bad = set(rule) - RULE_KEYS
    if bad:
        raise ValueError(f"{where}: unknown keys {', '.join(sorted(bad))}")
    mode = rule.get("mode", rule.get("action"))
    if not isinstance(mode, str) or mode not in RULE_MODES:
        raise ValueError(f"{where}: mode must be one of {', '.join(sorted(RULE_MODES))}")
    if rule.get("direction", "in") not in {"in", "out", "any"}:
        raise ValueError(f"{where}: direction must be in, out or any")
    if "when_contact" in rule and not isinstance(rule["when_contact"], bool):
        raise SettingsTypeError(f"{where}: when_contact must be true or false")
    for k in ("when_number", "agent_name", "play_file", "stall_dir"):
        if k in rule and not isinstance(rule[k], str):
            raise SettingsTypeError(f"{where}: {k} must be a string")
    return mode


AUTOMATION_KEYS = {"name", "on", "when", "extract", "do"}  # the rest are action params


def validate_automations(app: SettingsHost, rules: object) -> None:
    """Runtime automations: {name?, on: event glob, when?: {field: glob},
    extract?: {var: regex}, do: action, <action params>: str}. Same shape as
    the config file's [[automations]]; validated here so a bad rule is a 400,
    not a warning in the log at the moment it should have fired."""
    if not isinstance(rules, list):
        raise SettingsTypeError("automations must be a list")
    for i, rule in enumerate(rules):
        where = f"automations[{i}]"
        if not isinstance(rule, dict):
            raise SettingsTypeError(f"{where} must be an object")
        _validate_automation_shape(where, rule, app.automations.actions)
        _validate_delivery(app, where, rule)


def _validate_automation_shape(where: str, rule: dict[str, Any], actions: Iterable[str]) -> None:
    if not isinstance(rule.get("on"), str) or not rule["on"]:
        raise ValueError(f"{where}: on must be an event type (glob), e.g. sms.received")
    if rule.get("do") not in actions:
        raise ValueError(f"{where}: do must be one of {', '.join(sorted(actions))}")
    for key in ("when", "extract"):
        table = rule.get(key, {})
        if not isinstance(table, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in table.items()
        ):
            raise SettingsTypeError(f"{where}: {key} must map names to strings")
    _validate_patterns(where, rule.get("extract", {}))
    for key, value in rule.items():
        if key not in AUTOMATION_KEYS and not isinstance(value, str):
            raise SettingsTypeError(f"{where}: {key} must be a string")


def _validate_patterns(where: str, extract: dict[str, str]) -> None:
    for var, pattern in extract.items():
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{where}: extract.{var} is not a valid regex: {exc}") from exc


def _validate_delivery(app: SettingsHost, where: str, rule: dict[str, Any]) -> None:
    """Catch at save time what the action would only discover when it fires."""
    sinks = app.config.get("sinks", [])
    if rule["do"] == "telegram" and not (
        rule.get("bot_token") or any(s.get("adapter") == "telegram" for s in sinks)
    ):
        raise ValueError(
            f'{where}: telegram needs a [[sinks]] adapter = "telegram" entry '
            "(bot_token, chat_id) in the config, or bot_token/chat_id on the rule"
        )
    if rule["do"] == "webhook" and not str(rule.get("url", "")).startswith(("http://", "https://")):
        raise ValueError(f"{where}: webhook needs url (http:// or https://)")
    if rule["do"] == "play" and not rule.get("file"):
        raise ValueError(f"{where}: play needs file")


# setting -> (router attribute, coercion); "rules" is copied, not coerced.
_ROUTER_ATTRS: dict[str, tuple[str, type]] = {
    "incoming": ("incoming_mode", str),
    "outgoing": ("outgoing_mode", str),
    "agent_name": ("agent_name", str),
    "play_file": ("play_path", str),
    "stall_dir": ("stall_dir", str),
    "answer": ("answer_mode", str),
    "record_calls": ("record_calls", bool),
    "record_dir": ("record_dir", str),
}


def apply(app: SettingsHost, data: dict[str, Any]) -> None:
    r = app.router
    if "rules" in data:
        r.rules = list(data["rules"])
    if "automations" in data:
        app.automations.runtime = list(data["automations"])
    for key, (attr, coerce) in _ROUTER_ATTRS.items():
        if key in data:
            setattr(r, attr, coerce(data[key]))
    _apply_livekit(app, dict(data.get("livekit") or {}))


def _apply_livekit(app: SettingsHost, lk: dict[str, Any]) -> None:
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
        if lk.get(f):
            setattr(bridge, f, str(lk[f]))
