"""agent-bluetooth: one CLI for agents to drive a phone over Bluetooth.

The daemon runs the radio + LiveKit bridge + HTTP API:
    agent-bluetooth serve [config.toml]

Every other verb is a thin client over that daemon's HTTP API, so an agent
scripts the phone the same way it scripts a browser:

    agent-bluetooth status                 # phone + current calls, as JSON
    agent-bluetooth watch                  # live event stream, one JSON per line
    agent-bluetooth watch sms call         # only these event families
    agent-bluetooth call <number>          # place a call
    agent-bluetooth answer                 # answer the ringing call
    agent-bluetooth hangup                 # hang up / reject
    agent-bluetooth play <file.wav>        # answer and play a WAV into the line
    agent-bluetooth bridge                 # answer and move audio into a LiveKit room
    agent-bluetooth join [identity]        # print LiveKit url+token to join the call
    agent-bluetooth dtmf <digits>
    agent-bluetooth sms <to> <text...>     # send an SMS
    agent-bluetooth sim call|sms|...       # inject fake traffic (fake adapters)

Target a remote box with --host / --key (or AGENT_BLUETOOTH_HOST / _KEY).
Agents: run `agent-bluetooth guide` for the full recipe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

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
        sys.exit(f"error {e.code}: {e.read().decode().strip()}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach daemon at {args.host}: {e.reason}\nstart it with: agent-bluetooth serve")


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _watch(args: argparse.Namespace) -> None:
    families = set(args.families)
    url = f"{args.host.rstrip('/')}/api/events" + (f"?key={args.key}" if args.key else "")
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
        sys.exit(f"cannot reach daemon at {args.host}: {e.reason}")
    except KeyboardInterrupt:
        pass


def main() -> None:
    p = argparse.ArgumentParser(prog="agent-bluetooth", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.environ.get("AGENT_BLUETOOTH_HOST", "http://127.0.0.1:8080"))
    p.add_argument("--key", default=os.environ.get("AGENT_BLUETOOTH_KEY", ""))
    p.add_argument("--timeout", type=float, default=15)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the daemon")
    s.add_argument("config", nargs="?", default="config.toml")
    s.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("guide", help="full usage recipe for agents")
    sub.add_parser("status", help="phone + current calls as JSON")

    w = sub.add_parser("watch", help="stream events as JSON lines")
    w.add_argument("families", nargs="*", help="filter: phone call room sms playback")

    c = sub.add_parser("call", help="place an outgoing call")
    c.add_argument("number")
    sub.add_parser("answer", help="answer the ringing call")
    sub.add_parser("hangup", help="hang up / reject the current call")

    pl = sub.add_parser("play", help="answer and play a WAV into the line")
    pl.add_argument("file")
    pl.add_argument("--loop", action="store_true")
    pl.add_argument("--keep", action="store_true", help="don't hang up when the clip ends")
    pl.add_argument("--call-id", default=None)

    br = sub.add_parser("bridge", help="answer and move audio into a LiveKit room")
    br.add_argument("--call-id", default=None)

    j = sub.add_parser("join", help="print LiveKit url+token to join the current call room")
    j.add_argument("identity", nargs="?", default="cli")

    d = sub.add_parser("dtmf", help="send DTMF digits")
    d.add_argument("digits")
    v = sub.add_parser("volume", help="set speaker volume 0..15")
    v.add_argument("level", type=int)

    sm = sub.add_parser("sms", help="send an SMS")
    sm.add_argument("to")
    sm.add_argument("text", nargs="+")

    si = sub.add_parser("sim", help="inject fake traffic (fake adapters only)")
    si.add_argument("kind")
    si.add_argument("params", nargs="*", help="key=value pairs, e.g. number=+1555...")

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
    elif args.cmd == "call":
        _out(_req(args, "POST", "/api/calls", {"number": args.number}))
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
        _out(_req(args, "POST", "/api/calls/bridge", {"call_id": args.call_id} if args.call_id else {}))
    elif args.cmd == "join":
        status = _req(args, "GET", "/api/status")
        rooms = [c["room"] for c in status["calls"] if c.get("room")]
        if not rooms:
            sys.exit("no active call with a room; run `bridge` first")
        _out(_req(args, "POST", "/api/token", {"room": rooms[0], "identity": args.identity}))
    elif args.cmd == "dtmf":
        _out(_req(args, "POST", "/api/calls/dtmf", {"digits": args.digits}))
    elif args.cmd == "volume":
        _out(_req(args, "POST", "/api/volume", {"level": args.level}))
    elif args.cmd == "sms":
        _out(_req(args, "POST", "/api/sms", {"to": args.to, "text": " ".join(args.text)}))
    elif args.cmd == "sim":
        params = dict(kv.split("=", 1) for kv in args.params)
        _out(_req(args, "POST", f"/api/simulate/{args.kind}", params))


if __name__ == "__main__":
    main()
