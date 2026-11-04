"""One handler per API command of the CLI. `COMMANDS` maps the command name to it."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .cli_client import (
    fail,
    out,
    request,
    request_list,
    request_object,
    wait_for,
    watch,
)
from .guide import GUIDE

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    from .cli_client import JsonObject

    Handler = Callable[[argparse.Namespace], None]
    RuleEdit = Callable[[argparse.Namespace, list[JsonObject]], list[JsonObject]]

NUMBER_KEY_DIGITS = 10  # national part: +1 555... and 555... are the same number
TOLLFREE_PREFIXES = ("800", "888", "877", "866", "855", "844", "833")
UNKNOWN_CALLER_PRESETS = {"contacts-only": "reject", "screen-unknown": "stall"}
PRESETS_HELP = (
    "Presets: contacts-only (decline everyone not in contacts), screen-unknown "
    "(keep unknown numbers busy with stall_dir phrases), block-tollfree"
)
_TRUTHY = ("true", "1", "yes", "on")
_BOOLS = {"true": True, "false": False}


# helpers ---------------------------------------------------------------------
def _pairs(values: list[str]) -> dict[str, str]:
    result = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            fail(f"Expected key=value, got {item!r}", code=2)
        result[key] = value
    return result


def _digits(number: str) -> str:
    return "".join(c for c in number if c.isdigit())[-NUMBER_KEY_DIGITS:]


def _is_block(rule: JsonObject) -> bool:
    """A blocklist entry: one exact number, declined or kept busy."""
    pattern = rule.get("when_number", "")
    return (
        bool(pattern)
        and not any(c in pattern for c in "*?[")
        and rule.get("mode") in ("reject", "stall")
    )


def _is_unknown_rule(rule: JsonObject) -> bool:
    return rule.get("when_contact") is False and "when_number" not in rule


def _current_rules(args: argparse.Namespace) -> list[JsonObject]:
    return request_object(args, "GET", "/api/settings").get("rules", [])


def _save_rules(args: argparse.Namespace, rules: list[JsonObject]) -> JsonObject:
    return request_object(args, "POST", "/api/settings", {"rules": rules})


def _passthrough(method: str, path: str) -> Handler:
    """A command that is exactly one API call; POSTs send an empty object."""

    def handler(args: argparse.Namespace) -> None:
        out(request(args, method, path, {} if method == "POST" else None))

    return handler


# blocklist -------------------------------------------------------------------
def _without_blocks(rules: list[JsonObject], numbers: list[str]) -> list[JsonObject]:
    keys = {_digits(n) for n in numbers}
    return [r for r in rules if not (_is_block(r) and _digits(r["when_number"]) in keys)]


def _print_blocklist(args: argparse.Namespace, rules: list[JsonObject]) -> None:
    saved = _save_rules(args, rules)
    out({"blocked": [r for r in saved.get("rules", []) if _is_block(r)]})


def _numbers_from_file(path: str) -> list[str]:
    with Path(path).open() as f:
        return [ln.split("#")[0].strip() for ln in f if ln.split("#")[0].strip()]


def _block(args: argparse.Namespace) -> None:
    current = _current_rules(args)
    numbers = list(args.numbers) + (_numbers_from_file(args.file) if args.file else [])
    if not numbers:
        fail("Give at least one number (or --file)", code=2)
    mode = "stall" if args.stall else "reject"
    added = [{"when_number": n, "mode": mode} for n in numbers]
    _print_blocklist(args, added + _without_blocks(current, numbers))


def _unblock(args: argparse.Namespace) -> None:
    _print_blocklist(args, _without_blocks(_current_rules(args), list(args.numbers)))


def _blocked(args: argparse.Namespace) -> None:
    out([r for r in _current_rules(args) if _is_block(r)])


# rules -----------------------------------------------------------------------
def _rule_add(args: argparse.Namespace, current: list[JsonObject]) -> list[JsonObject]:
    rule: JsonObject = {}
    for key, value in _pairs(args.values).items():
        rule[key] = _BOOLS.get(value.lower(), value) if key == "when_contact" else value
    return [rule, *current] if args.first else [*current, rule]


def _rule_remove(args: argparse.Namespace, current: list[JsonObject]) -> list[JsonObject]:
    values = args.values
    if len(values) != 1 or not values[0].isdigit() or int(values[0]) >= len(current):
        fail(f"rules remove needs an index 0..{len(current) - 1}", code=2)
    return [r for i, r in enumerate(current) if i != int(values[0])]


def _rule_clear(_: argparse.Namespace, __: list[JsonObject]) -> list[JsonObject]:
    return []


def _rule_preset(args: argparse.Namespace, current: list[JsonObject]) -> list[JsonObject]:
    name = args.values[0] if args.values else ""
    blocks = [r for r in current if _is_block(r)]
    if name == "block-tollfree":
        toll = [{"when_number": f"+1{p}*", "mode": "reject"} for p in TOLLFREE_PREFIXES]
        return blocks + [r for r in current if r not in blocks and r not in toll] + toll
    mode = UNKNOWN_CALLER_PRESETS.get(name)
    if mode is None:
        fail(PRESETS_HELP, code=2)
    others = [r for r in current if not _is_block(r) and not _is_unknown_rule(r)]
    return [*blocks, {"when_contact": False, "mode": mode}, *others]


_RULE_EDITS: dict[str, RuleEdit] = {
    "add": _rule_add,
    "remove": _rule_remove,
    "clear": _rule_clear,
    "preset": _rule_preset,
}


def _rules(args: argparse.Namespace) -> None:
    current = _current_rules(args)
    if args.action == "list":
        out(current)
        return
    saved = _save_rules(args, _RULE_EDITS[args.action](args, current))
    out({"rules": saved.get("rules", []), "summary": saved.get("summary", "")})


# settings --------------------------------------------------------------------
def _settings_body(values: list[str]) -> JsonObject:
    body: JsonObject = {}
    for key, value in _pairs(values).items():
        if key.startswith("livekit."):  # livekit.url=wss://... -> {"livekit": {"url": ...}}
            body.setdefault("livekit", {})[key.removeprefix("livekit.")] = value
        elif key == "record_calls":
            body[key] = value.lower() in _TRUTHY
        else:
            body[key] = value
    return body


def _settings(args: argparse.Namespace) -> None:
    if args.action != "set":
        out(request(args, "GET", "/api/settings"))
        return
    if not args.values:
        fail("settings set needs at least one key=value", code=2)
    out(request(args, "POST", "/api/settings", _settings_body(args.values)))


# calls -----------------------------------------------------------------------
def _call(args: argparse.Namespace) -> None:
    body = {"number": args.number}
    optional = {"agent_name": args.agent, "task": args.task, "room": args.room}
    body.update({key: value for key, value in optional.items() if value})
    out(request(args, "POST", "/api/calls", body))


def _play(args: argparse.Namespace) -> None:
    body: JsonObject = {"file": args.file, "loop": args.loop, "hangup_after": not args.keep}
    if args.call_id:
        body["call_id"] = args.call_id
    out(request(args, "POST", "/api/calls/play", body))


def _bridge(args: argparse.Namespace) -> None:
    out(
        request(
            args, "POST", "/api/calls/bridge", {"call_id": args.call_id} if args.call_id else {}
        )
    )


def _join(args: argparse.Namespace) -> None:
    status = request_object(args, "GET", "/api/status")
    rooms = [c["room"] for c in status["calls"] if c.get("room")]
    if not rooms:
        fail("No active call with a room; run bridge first")
    out(request(args, "POST", "/api/token", {"room": rooms[0], "identity": args.identity}))


def _dtmf(args: argparse.Namespace) -> None:
    out(request(args, "POST", "/api/calls/dtmf", {"digits": args.digits}))


def _volume(args: argparse.Namespace) -> None:
    out(request(args, "POST", "/api/volume", {"level": args.level}))


# messages, directory, diagnostics -------------------------------------------
def _sms(args: argparse.Namespace) -> None:
    if args.to == "list":
        if args.text:
            fail("sms list accepts only -n <limit>", code=2)
        out(request(args, "GET", f"/api/sms?limit={args.n}"))
        return
    if not args.text:
        fail("sms needs a destination and message text", code=2)
    out(request(args, "POST", "/api/sms", {"to": args.to, "text": " ".join(args.text)}))


def _contacts(args: argparse.Namespace) -> None:
    q = args.query.lower()
    items = request_list(args, "/api/contacts")
    out([c for c in items if q in c["name"].lower() or any(q in n for n in c["numbers"])])


def _logs(args: argparse.Namespace) -> None:
    lines = request_object(args, "GET", "/api/debug")["logs"][-args.n :]
    sys.stdout.write("\n".join(lines) + "\n")


def _sim(args: argparse.Namespace) -> None:
    out(request(args, "POST", f"/api/simulate/{args.kind}", _pairs(args.params)))


def _guide(_: argparse.Namespace) -> None:
    sys.stdout.write(GUIDE + "\n")


def _watch(args: argparse.Namespace) -> None:
    watch(args, set(args.families))


def _wait(args: argparse.Namespace) -> None:
    wait_for(args, args.event_type, _pairs(args.match), args.wait_timeout)


COMMANDS: dict[str, Handler] = {
    "guide": _guide,
    "status": _passthrough("GET", "/api/status"),
    "watch": _watch,
    "wait": _wait,
    "block": _block,
    "unblock": _unblock,
    "blocked": _blocked,
    "rules": _rules,
    "settings": _settings,
    "call": _call,
    "answer": _passthrough("POST", "/api/calls/answer"),
    "hangup": _passthrough("POST", "/api/calls/hangup"),
    "play": _play,
    "bridge": _bridge,
    "join": _join,
    "dtmf": _dtmf,
    "volume": _volume,
    "sms": _sms,
    "sim": _sim,
    "contacts": _contacts,
    "history": _passthrough("GET", "/api/calls/history"),
    "sync": _passthrough("POST", "/api/directory/sync"),
    "debug": _passthrough("GET", "/api/debug"),
    "logs": _logs,
}
