"""Drive a phone through the daemon HTTP API. Run guide for recipes."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import urllib.error
import urllib.request

import aiohttp

from .guide import GUIDE


def _req(args: argparse.Namespace, method: str, path: str, body: dict | None = None):
    url = f"{args.host.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if args.key:
        req.add_header("Authorization", f"Bearer {args.key}")
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        _fail(e.read().decode().strip(), status=e.code)
    except urllib.error.URLError as e:
        _fail(
            f"Cannot reach daemon at {args.host}: {e.reason}. Start agent-bluetooth serve."
        )
    except (TimeoutError, OSError) as e:
        _fail(f"Request to {args.host} failed: {e}")


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _fail(message: str, code: int = 1, **details) -> None:
    try:
        message = json.loads(message).get("error", message)
    except (ValueError, AttributeError):
        pass
    _out({"error": message, **details})
    raise SystemExit(code)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _fail(message, code=2)


def _digits(number: str) -> str:
    return "".join(c for c in number if c.isdigit())[-10:]


def _is_block(rule: dict) -> bool:
    """A blocklist entry: one exact number, declined or kept busy."""
    pattern = rule.get("when_number", "")
    return (
        bool(pattern)
        and not any(c in pattern for c in "*?[")
        and rule.get("mode") in ("reject", "stall")
    )


def _is_unknown_rule(rule: dict) -> bool:
    return rule.get("when_contact") is False and "when_number" not in rule


def _pairs(values: list[str]) -> dict[str, str]:
    result = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            _fail(f"Expected key=value, got {item!r}", code=2)
        result[key] = value
    return result


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return number


def _matches(event: dict, filters: dict[str, str]) -> bool:
    for key, expected in filters.items():
        value = (
            event if key.startswith("data.") or key in {"type", "ts"} else event["data"]
        )
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return False
            value = value[part]
        actual = json.dumps(value) if not isinstance(value, str) else value
        if actual != expected:
            return False
    return True


async def _wait(args: argparse.Namespace) -> None:
    filters = _pairs(args.match)
    headers = {"Accept": "text/event-stream"}
    if args.key:
        headers["Authorization"] = f"Bearer {args.key}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=args.wait_timeout), headers=headers
        ) as client:
            async with client.get(f"{args.host.rstrip('/')}/api/events") as response:
                if response.status >= 400:
                    _fail(await response.text(), status=response.status)
                async for raw in response.content:
                    line = raw.decode().strip()
                    if line.startswith("data:"):
                        event = json.loads(line[5:].strip())
                        if event["type"] == args.event_type and _matches(
                            event, filters
                        ):
                            _out(event)
                            return
                _fail("Event stream closed before a matching event arrived")
    except TimeoutError:
        _fail(
            f"Timed out after {args.wait_timeout:g}s waiting for {args.event_type}",
            code=1,
        )
    except (aiohttp.ClientError, OSError) as e:
        _fail(f"Cannot read events from {args.host}: {e}")


def _watch(args: argparse.Namespace) -> None:
    families = set(args.families)
    url = f"{args.host.rstrip('/')}/api/events"
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    if args.key:
        req.add_header("Authorization", f"Bearer {args.key}")
    try:
        with urllib.request.urlopen(req) as stream:
            for raw in stream:
                line = raw.decode().strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                if not families or event["type"].split(".")[0] in families:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
    except urllib.error.URLError as e:
        _fail(f"Cannot reach daemon at {args.host}: {e.reason}")
    except OSError as e:
        _fail(f"Cannot read events from {args.host}: {e}")
    except KeyboardInterrupt:
        pass


def main() -> None:
    p = _Parser(
        prog="agent-bluetooth",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--host",
        default=os.environ.get("AGENT_BLUETOOTH_HOST", "http://127.0.0.1:8080"),
    )
    p.add_argument("--key", default=os.environ.get("AGENT_BLUETOOTH_KEY", ""))
    p.add_argument("--timeout", type=_positive, default=15)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the daemon")
    s.add_argument("config", nargs="?", default="config.toml")
    s.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("guide", help="full usage recipe for agents")
    sub.add_parser("status", help="phone + current calls as JSON")

    w = sub.add_parser("watch", help="stream events as JSON lines")
    w.add_argument("families", nargs="*", help="filter: phone call room sms playback")

    wait = sub.add_parser(
        "wait", help="wait for one matching live event; timeout exits 1"
    )
    wait.add_argument("event_type", help="exact type, e.g. call.active")
    wait.add_argument("--timeout", dest="wait_timeout", type=_positive, default=60)
    wait.add_argument(
        "--match",
        action="append",
        default=[],
        help="data key=value; repeat for AND; nested: call.number=+1555...",
    )

    block = sub.add_parser(
        "block", help="block numbers (declined, or kept busy with --stall)"
    )
    block.add_argument("numbers", nargs="*")
    block.add_argument(
        "--file", help="import numbers from a text file, one per line (# comments ok)"
    )
    block.add_argument(
        "--stall",
        action="store_true",
        help="keep them busy with stall_dir phrases instead of declining",
    )
    unblock = sub.add_parser("unblock", help="remove numbers from the blocklist")
    unblock.add_argument("numbers", nargs="+")
    sub.add_parser("blocked", help="list blocked numbers")
    rules = sub.add_parser(
        "rules",
        help="route calls by who calls (first match wins)",
        description="Examples: rules add when_contact=false mode=play play_file=/abs/stall.wav | "
        "rules add when_contact=true mode=agent agent_name=receptionist | rules list | rules remove 0",
    )
    rules.add_argument(
        "action",
        choices=["list", "add", "remove", "clear", "preset"],
        nargs="?",
        default="list",
    )
    rules.add_argument(
        "values",
        nargs="*",
        help="add: key=value (direction, when_number, when_contact, mode, agent_name, play_file); remove: index",
    )
    rules.add_argument(
        "--first", action="store_true", help="add: put the rule before existing ones"
    )
    settings = sub.add_parser("settings", help="show or update call handling")
    settings.add_argument("action", nargs="?", choices=["set"])
    settings.add_argument(
        "values",
        nargs="*",
        help="incoming/outgoing=app|agent|play|record|ignore; incoming=reject; agent_name, play_file, answer=auto|on_join|manual",
    )

    c = sub.add_parser(
        "call",
        help="place an outgoing call",
        epilog='call +15551234567 --agent phone-agent --task "Book a table for 2 at 7pm"',
    )
    c.add_argument("number")
    c.add_argument(
        "--agent", default="", help="LiveKit agent to dispatch for this call"
    )
    c.add_argument("--task", default="", help="what the agent should get done")
    c.add_argument("--room", default="", help="join the phone into this existing room")
    sub.add_parser("answer", help="answer the ringing call")
    sub.add_parser("hangup", help="hang up / reject the current call")

    pl = sub.add_parser("play", help="answer and play a WAV into the line")
    pl.add_argument("file")
    pl.add_argument("--loop", action="store_true")
    pl.add_argument(
        "--keep", action="store_true", help="don't hang up when the clip ends"
    )
    pl.add_argument("--call-id", default=None)

    br = sub.add_parser("bridge", help="answer and move audio into a LiveKit room")
    br.add_argument("--call-id", default=None)

    j = sub.add_parser(
        "join", help="print LiveKit url+token to join the current call room"
    )
    j.add_argument("identity", nargs="?", default="cli")

    d = sub.add_parser("dtmf", help="send DTMF digits")
    d.add_argument("digits")
    v = sub.add_parser("volume", help="set speaker volume 0..15")
    v.add_argument("level", type=int)

    sm = sub.add_parser("sms", help="send: sms <to> <text>; read: sms list [-n 20]")
    sm.add_argument("to")
    sm.add_argument("text", nargs="*")
    sm.add_argument("-n", type=int, default=20, help="list limit, 1..1000")

    si = sub.add_parser("sim", help="inject fake traffic (fake adapters only)")
    si.add_argument("kind")
    si.add_argument("params", nargs="*", help="key=value pairs, e.g. number=+1555...")

    co = sub.add_parser("contacts", help="phonebook from the last sync")
    co.add_argument("query", nargs="?", default="", help="filter by name or number")
    sub.add_parser("history", help="recent calls from the phone")
    sub.add_parser("sync", help="re-pull contacts + history from the phone now")
    sub.add_parser(
        "debug", help="full diagnostic dump: phone, radio, sync, calls, events, logs"
    )
    lg = sub.add_parser("logs", help="daemon log tail")
    lg.add_argument("-n", type=int, default=100)

    args = p.parse_args()

    if args.cmd == "serve":
        from .app import run_config

        run_config(args.config, args.verbose)
        return
    if args.cmd == "guide":
        print(GUIDE)
        return

    if args.cmd == "status":
        _out(_req(args, "GET", "/api/status"))
    elif args.cmd == "watch":
        _watch(args)
    elif args.cmd == "wait":
        try:
            asyncio.run(_wait(args))
        except KeyboardInterrupt:
            _fail("Interrupted", code=130)
    elif args.cmd in ("block", "unblock", "blocked"):
        current_rules = _req(args, "GET", "/api/settings").get("rules", [])
        if args.cmd == "blocked":
            _out([r for r in current_rules if _is_block(r)])
        else:
            numbers = list(args.numbers)
            if args.cmd == "block" and args.file:
                with open(args.file) as f:
                    numbers += [
                        ln.split("#")[0].strip() for ln in f if ln.split("#")[0].strip()
                    ]
            if not numbers:
                _fail("Give at least one number (or --file)", code=2)
            keys = {_digits(n) for n in numbers}
            kept = [
                r
                for r in current_rules
                if not (_is_block(r) and _digits(r["when_number"]) in keys)
            ]
            if args.cmd == "block":
                mode = "stall" if args.stall else "reject"
                kept = [{"when_number": n, "mode": mode} for n in numbers] + kept
            saved = _req(args, "POST", "/api/settings", {"rules": kept})
            _out({"blocked": [r for r in saved.get("rules", []) if _is_block(r)]})
    elif args.cmd == "rules" and args.action == "preset":
        current_rules = _req(args, "GET", "/api/settings").get("rules", [])
        name = args.values[0] if args.values else ""
        blocks = [r for r in current_rules if _is_block(r)]
        others = [
            r for r in current_rules if not _is_block(r) and not _is_unknown_rule(r)
        ]
        if name == "contacts-only":
            new_rules = blocks + [{"when_contact": False, "mode": "reject"}] + others
        elif name == "screen-unknown":
            new_rules = blocks + [{"when_contact": False, "mode": "stall"}] + others
        elif name == "block-tollfree":
            toll = [
                {"when_number": f"+1{p}*", "mode": "reject"}
                for p in ("800", "888", "877", "866", "855", "844", "833")
            ]
            new_rules = (
                blocks
                + [r for r in current_rules if r not in blocks and r not in toll]
                + toll
            )
        else:
            _fail(
                "Presets: contacts-only (decline everyone not in contacts), screen-unknown "
                "(keep unknown numbers busy with stall_dir phrases), block-tollfree",
                code=2,
            )
        saved = _req(args, "POST", "/api/settings", {"rules": new_rules})
        _out({"rules": saved.get("rules", []), "summary": saved.get("summary", "")})
    elif args.cmd == "rules":
        current_rules = _req(args, "GET", "/api/settings").get("rules", [])
        if args.action == "list":
            _out(current_rules)
        else:
            if args.action == "add":
                rule: dict = {}
                for key, value in _pairs(args.values).items():
                    rule[key] = (
                        {"true": True, "false": False}.get(value.lower(), value)
                        if key == "when_contact"
                        else value
                    )
                new_rules = (
                    [rule, *current_rules] if args.first else [*current_rules, rule]
                )
            elif args.action == "remove":
                if (
                    len(args.values) != 1
                    or not args.values[0].isdigit()
                    or int(args.values[0]) >= len(current_rules)
                ):
                    _fail(
                        f"rules remove needs an index 0..{len(current_rules) - 1}",
                        code=2,
                    )
                new_rules = [
                    r for i, r in enumerate(current_rules) if i != int(args.values[0])
                ]
            else:
                new_rules = []
            saved = _req(args, "POST", "/api/settings", {"rules": new_rules})
            _out({"rules": saved.get("rules", []), "summary": saved.get("summary", "")})
    elif args.cmd == "settings":
        if args.action == "set":
            if not args.values:
                p.error("settings set needs at least one key=value")
            body: dict = {}
            for key, value in _pairs(args.values).items():
                if key.startswith(
                    "livekit."
                ):  # livekit.url=wss://... -> {"livekit": {"url": ...}}
                    body.setdefault("livekit", {})[key.removeprefix("livekit.")] = value
                else:
                    body[key] = value
            _out(_req(args, "POST", "/api/settings", body))
        else:
            _out(_req(args, "GET", "/api/settings"))
    elif args.cmd == "call":
        body = {"number": args.number}
        for key, value in (
            ("agent_name", args.agent),
            ("task", args.task),
            ("room", args.room),
        ):
            if value:
                body[key] = value
        _out(_req(args, "POST", "/api/calls", body))
    elif args.cmd == "answer":
        _out(_req(args, "POST", "/api/calls/answer", {}))
    elif args.cmd == "hangup":
        _out(_req(args, "POST", "/api/calls/hangup", {}))
    elif args.cmd == "play":
        body = {"file": args.file, "loop": args.loop, "hangup_after": not args.keep}
        if args.call_id:
            body["call_id"] = args.call_id
        _out(_req(args, "POST", "/api/calls/play", body))
    elif args.cmd == "bridge":
        _out(
            _req(
                args,
                "POST",
                "/api/calls/bridge",
                {"call_id": args.call_id} if args.call_id else {},
            )
        )
    elif args.cmd == "join":
        status = _req(args, "GET", "/api/status")
        rooms = [c["room"] for c in status["calls"] if c.get("room")]
        if not rooms:
            _fail("No active call with a room; run bridge first")
        _out(
            _req(
                args,
                "POST",
                "/api/token",
                {"room": rooms[0], "identity": args.identity},
            )
        )
    elif args.cmd == "dtmf":
        _out(_req(args, "POST", "/api/calls/dtmf", {"digits": args.digits}))
    elif args.cmd == "volume":
        _out(_req(args, "POST", "/api/volume", {"level": args.level}))
    elif args.cmd == "sms":
        if args.to == "list":
            if args.text:
                p.error("sms list accepts only -n <limit>")
            _out(_req(args, "GET", f"/api/sms?limit={args.n}"))
        else:
            if not args.text:
                p.error("sms needs a destination and message text")
            _out(
                _req(
                    args,
                    "POST",
                    "/api/sms",
                    {"to": args.to, "text": " ".join(args.text)},
                )
            )
    elif args.cmd == "contacts":
        q = args.query.lower()
        items = _req(args, "GET", "/api/contacts")
        _out(
            [
                c
                for c in items
                if q in c["name"].lower() or any(q in n for n in c["numbers"])
            ]
        )
    elif args.cmd == "history":
        _out(_req(args, "GET", "/api/calls/history"))
    elif args.cmd == "sync":
        _out(_req(args, "POST", "/api/directory/sync", {}))
    elif args.cmd == "debug":
        _out(_req(args, "GET", "/api/debug"))
    elif args.cmd == "logs":
        print("\n".join(_req(args, "GET", "/api/debug")["logs"][-args.n :]))
    elif args.cmd == "sim":
        params = _pairs(args.params)
        _out(_req(args, "POST", f"/api/simulate/{args.kind}", params))


if __name__ == "__main__":
    main()
