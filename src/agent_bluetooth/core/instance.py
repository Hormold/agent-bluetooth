"""One config file, one or many independent daemons.

A plain config is a single instance named "default" (state in ~/.agent-bluetooth,
exactly as before). Add `[[instances]]` and the same file describes a farm: every
instance is the base config deep-merged with its own table, and gets its own
radio, keystore, settings, recordings, HTTP port, LiveKit room prefix and phone-
visible name. Each instance runs as its own process (`serve --instance NAME`, a
systemd template unit, or `agent-bluetooth farm`), so a wedged dongle restarts
only its own daemon.

    [[instances]]
    name = "pixel"
    transport = "usb:1-1.2+sco=2"     # shorthand for [bluetooth] transport

    [[instances]]
    name = "iphone"
    transport = "usb:1-1.3+sco=2"
    http = { port = 8090 }            # any section can be overridden
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

DEFAULT = "default"
BASE_PORT = 8080
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_SHORTHAND = {
    "transport": ("bluetooth", "transport"),
    "state_dir": ("instance", "state_dir"),
}


class ConfigError(ValueError):
    pass


def names(raw: dict[str, Any]) -> list[str]:
    return [str(i.get("name", "")) for i in raw.get("instances", [])] or [DEFAULT]


def resolve(raw: dict[str, Any], instance: str | None = None) -> dict[str, Any]:
    """The full config one daemon runs with, every per-instance default filled in."""
    entries: list[dict[str, Any]] = raw.get("instances", [])
    base = {k: v for k, v in raw.items() if k != "instances"}
    if not entries:
        if instance not in (None, DEFAULT):
            raise ConfigError(f"no [[instances]] in config, so no instance {instance!r}")
        return _with_defaults(base, DEFAULT, 0, farm=False)
    _check_farm(entries)
    known = names(raw)
    if instance is None:
        if len(entries) > 1:
            raise ConfigError(f"config has {len(entries)} instances; pick one: {', '.join(known)}")
        instance = known[0]
    if instance not in known:
        raise ConfigError(f"unknown instance {instance!r} (known: {', '.join(known)})")
    index = known.index(instance)
    own = _expand(entries[index])
    # A port in the shared base is the farm's first port: instance i gets base + i.
    port = (
        own.get("http", {}).get("port") or int(base.get("http", {}).get("port", BASE_PORT)) + index
    )
    cfg = _merge(base, own)
    cfg.setdefault("http", {})["port"] = int(port)
    return _with_defaults(cfg, instance, index, farm=True)


def resolve_all(raw: dict[str, Any]) -> list[dict[str, Any]]:
    configs = [resolve(raw, name) for name in names(raw)]
    _check_unique(configs, ("http", "port"), "HTTP port")
    _check_unique(configs, ("bluetooth", "transport"), "Bluetooth transport")
    _check_unique(configs, ("bluetooth", "keystore"), "keystore")
    _check_unique(configs, ("directory", "cache_file"), "phonebook cache")
    _check_unique(configs, ("instance", "state_dir"), "state_dir")
    return configs


def _check_farm(entries: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for entry in entries:
        name = str(entry.get("name", ""))
        if not _NAME.match(name):
            raise ConfigError(f"instance name {name!r}: use 1-32 of a-z 0-9 -, starting alnum")
        if name in seen:
            raise ConfigError(f"instance {name!r} is listed twice")
        seen.add(name)


def _check_unique(configs: list[dict[str, Any]], path: tuple[str, str], what: str) -> None:
    owners: dict[str, str] = {}
    for cfg in configs:
        value = str(cfg.get(path[0], {}).get(path[1], ""))
        name = cfg["instance"]["name"]
        if value and (other := owners.setdefault(value, name)) != name:
            raise ConfigError(f"instances {other!r} and {name!r} share {what} {value!r}")


def _expand(entry: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in entry.items() if k not in _SHORTHAND and k != "name"}
    for key, (section, field) in _SHORTHAND.items():
        if key in entry:
            out.setdefault(section, {})[field] = entry[key]
    return out


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _with_defaults(cfg: dict[str, Any], name: str, index: int, *, farm: bool) -> dict[str, Any]:
    home = Path("~/.agent-bluetooth")
    inst = cfg.setdefault("instance", {})
    inst["name"] = name
    state = Path(inst.get("state_dir") or (home / "instances" / name if farm else home))
    inst["state_dir"] = str(state)
    bt = cfg.setdefault("bluetooth", {})
    bt.setdefault("keystore", str(state / "keys.json"))
    bt.setdefault("name", f"Agent Bluetooth {name}" if farm else "Agent Bluetooth")
    cfg.setdefault("settings_file", str(state / "settings.json"))
    cfg.setdefault("routing", {}).setdefault("record_dir", str(state / "recordings"))
    cfg.setdefault("directory", {}).setdefault("cache_file", str(state / "directory.json"))
    cfg.setdefault("http", {}).setdefault("port", BASE_PORT + index)  # farm: set by resolve
    bridge = cfg.setdefault("bridge", {"adapter": "null"})
    bridge.setdefault("room_prefix", f"call-{name}-" if farm else "call-")
    return cfg
