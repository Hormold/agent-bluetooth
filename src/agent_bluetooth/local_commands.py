"""CLI commands that run on this machine instead of calling a daemon's API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import TYPE_CHECKING, TypeVar

from .config import load_raw
from .core.instance import ConfigError, resolve, resolve_all

if TYPE_CHECKING:
    import argparse

P = TypeVar("P", bound="argparse.ArgumentParser")
DEFAULT_CONFIG = os.environ.get("AGENT_BLUETOOTH_CONFIG", "config.toml")


def add_parsers(sub: argparse._SubParsersAction[P]) -> None:
    s = sub.add_parser("serve", help="run one daemon (one instance of the config)")
    s.add_argument("config", nargs="?", help=f"config file (default {DEFAULT_CONFIG})")
    s.add_argument("--instance", dest="serve_instance", help="which [[instances]] entry to run")
    s.add_argument("-v", "--verbose", action="store_true")

    f = sub.add_parser("farm", help="run every instance of the config, one process each")
    f.add_argument("config", nargs="?", help=f"config file (default {DEFAULT_CONFIG})")
    f.add_argument("only", nargs="*", help="instance names to run (default: all)")
    f.add_argument("-v", "--verbose", action="store_true")

    i = sub.add_parser("instances", help="instances in the config: port, transport, state dir")
    i.add_argument("config", nargs="?", help=f"config file (default {DEFAULT_CONFIG})")

    sub.add_parser("radios", help="USB Bluetooth dongles plugged in, with a transport for each")


def run(args: argparse.Namespace) -> bool:
    """Handle a local command; False if `args.cmd` isn't one."""
    handler = _HANDLERS.get(args.cmd)
    if handler is None:
        return False
    try:
        handler(args)
    except ConfigError as e:
        raise SystemExit(f"config: {e}") from e
    return True


def resolve_target(args: argparse.Namespace) -> None:
    """`-i NAME` on an API command: talk to that instance's daemon, port and key from config."""
    cfg = resolve(load_raw(args.config or DEFAULT_CONFIG), args.instance)
    http = cfg["http"]
    host = http.get("host", "127.0.0.1")
    host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host  # noqa: S104 - matching, not binding
    args.host = f"http://{host}:{http['port']}"
    args.key = args.key or http.get("api_key", "")


def _serve(args: argparse.Namespace) -> None:
    from .app import run_config  # noqa: PLC0415 - keeps radio/LiveKit imports out of API commands

    run_config(
        args.config or DEFAULT_CONFIG, args.serve_instance or args.instance, verbose=args.verbose
    )


def _farm(args: argparse.Namespace) -> None:
    from .farm import supervise  # noqa: PLC0415 - only the supervisor needs it

    path = args.config or DEFAULT_CONFIG
    raw = load_raw(path)
    known = [c["instance"]["name"] for c in resolve_all(raw)]  # fail fast on clashes
    if unknown := set(args.only) - set(known):
        raise ConfigError(f"unknown instance(s) {', '.join(sorted(unknown))}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s [farm] %(message)s")
    asyncio.run(supervise(path, raw, args.only, verbose=args.verbose))


def _instances(args: argparse.Namespace) -> None:
    rows = [
        {
            "name": c["instance"]["name"],
            "http": f"{c['http'].get('host', '127.0.0.1')}:{c['http']['port']}",
            "transport": c["bluetooth"].get("transport", ""),
            "bluetooth_name": c["bluetooth"]["name"],
            "state_dir": c["instance"]["state_dir"],
        }
        for c in resolve_all(load_raw(args.config or DEFAULT_CONFIG))
    ]
    _print_json(rows)


def _radios(_: argparse.Namespace) -> None:
    from .adapters.bumble.usb_devices import list_radios  # noqa: PLC0415 - needs libusb

    _print_json([r.to_dict() for r in list_radios()])


def _print_json(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, indent=2) + "\n")


_HANDLERS = {"serve": _serve, "farm": _farm, "instances": _instances, "radios": _radios}
