"""Drive a phone through the daemon HTTP API. Run guide for recipes."""

from __future__ import annotations

import argparse
import math
import os
from typing import NoReturn

from . import local_commands
from .cli_client import fail
from .cli_commands import COMMANDS

DEFAULT_TIMEOUT_S = 15.0
DEFAULT_WAIT_TIMEOUT_S = 60.0
DEFAULT_SMS_LIST = 20
DEFAULT_LOG_LINES = 100


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        fail(message, code=2)


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return number


def _add_global_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--host",
        default=os.environ.get("AGENT_BLUETOOTH_HOST", "http://127.0.0.1:8080"),
    )
    p.add_argument("--key", default=os.environ.get("AGENT_BLUETOOTH_KEY", ""))
    p.add_argument("--timeout", type=_positive, default=DEFAULT_TIMEOUT_S)
    p.add_argument(
        "-i",
        "--instance",
        default=os.environ.get("AGENT_BLUETOOTH_INSTANCE"),
        help="farm instance to talk to; host and key come from --config",
    )
    p.add_argument("-c", "--config", help="config file for --instance (default config.toml)")


def _add_event_parsers(sub: argparse._SubParsersAction[_Parser]) -> None:
    sub.add_parser("guide", help="full usage recipe for agents")
    sub.add_parser("status", help="phone + current calls as JSON")

    w = sub.add_parser("watch", help="stream events as JSON lines")
    w.add_argument("families", nargs="*", help="filter: phone call room sms playback")

    wait = sub.add_parser("wait", help="wait for one matching live event; timeout exits 1")
    wait.add_argument("event_type", help="exact type, e.g. call.active")
    wait.add_argument(
        "--timeout", dest="wait_timeout", type=_positive, default=DEFAULT_WAIT_TIMEOUT_S
    )
    wait.add_argument(
        "--match",
        action="append",
        default=[],
        help="data key=value; repeat for AND; nested: call.number=+1555...",
    )


def _add_routing_parsers(sub: argparse._SubParsersAction[_Parser]) -> None:
    block = sub.add_parser("block", help="block numbers (declined, or kept busy with --stall)")
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
        help="incoming/outgoing=app|agent|play|ignore|stall; incoming=reject; agent_name, play_file, answer=auto|on_join|manual; record_calls=true|false, record_dir=PATH",
    )


def _add_call_parsers(sub: argparse._SubParsersAction[_Parser]) -> None:
    c = sub.add_parser(
        "call",
        help="place an outgoing call",
        epilog='call +15551234567 --agent phone-agent --task "Book a table for 2 at 7pm"',
    )
    c.add_argument("number")
    c.add_argument("--agent", default="", help="LiveKit agent to dispatch for this call")
    c.add_argument("--task", default="", help="what the agent should get done")
    c.add_argument("--room", default="", help="join the phone into this existing room")
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
    say = sub.add_parser(
        "say",
        help="tell the agent on the call what to say next",
        epilog='say "Make it 8 instead, and ask for a table outside"',
    )
    say.add_argument("text", nargs="+")
    say.add_argument("--call-id", default=None)
    att = sub.add_parser(
        "attention",
        help="raise Agent Phone's alert card: a person is on, a question, or clear",
        epilog='attention question "What is your account ZIP code?"   attention clear',
    )
    att.add_argument("kind", choices=["person", "question", "clear"])
    att.add_argument("text", nargs="*")
    att.add_argument("--call-id", default="")
    asst = sub.add_parser(
        "assistant",
        help="press the headset's assistant button (Siri / Gemini); experimental",
    )
    asst.add_argument("state", nargs="?", choices=["on", "off"], default="on")
    v = sub.add_parser("volume", help="set speaker volume 0..15")
    v.add_argument("level", type=int)


def _add_phone_data_parsers(sub: argparse._SubParsersAction[_Parser]) -> None:
    sm = sub.add_parser("sms", help="send: sms <to> <text>; read: sms list [-n 20]")
    sm.add_argument("to")
    sm.add_argument("text", nargs="*")
    sm.add_argument("-n", type=int, default=DEFAULT_SMS_LIST, help="list limit, 1..1000")

    otp = sub.add_parser(
        "otp",
        help="forward one-time codes from incoming SMS: otp on [--sms NUMBER | --webhook URL]",
        description="Installs an automation that extracts the 4-8 digit code from every "
        "incoming SMS and forwards it: to the config's Telegram sink (default), to a "
        "phone number by SMS, or to a webhook. Android only: iMessage, RCS and MMS never "
        "reach the daemon.",
    )
    otp.add_argument("action", nargs="?", choices=["on", "off"], help="omit to show the rule")
    otp.add_argument("--sms", metavar="NUMBER", help="text the code to this number")
    otp.add_argument("--webhook", metavar="URL", help="POST {text, vars} to this URL")

    si = sub.add_parser("sim", help="inject fake traffic (fake adapters only)")
    si.add_argument("kind")
    si.add_argument("params", nargs="*", help="key=value pairs, e.g. number=+1555...")

    co = sub.add_parser("contacts", help="phonebook from the last sync")
    co.add_argument("query", nargs="?", default="", help="filter by name or number")
    sub.add_parser("history", help="recent calls from the phone")
    sub.add_parser("sync", help="re-pull contacts + history from the phone now")
    sub.add_parser("debug", help="full diagnostic dump: phone, radio, sync, calls, events, logs")
    lg = sub.add_parser("logs", help="daemon log tail")
    lg.add_argument("-n", type=int, default=DEFAULT_LOG_LINES)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="agent-bluetooth",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_global_options(p)
    sub = p.add_subparsers(dest="cmd", required=True)
    local_commands.add_parsers(sub)
    _add_event_parsers(sub)
    _add_routing_parsers(sub)
    _add_call_parsers(sub)
    _add_phone_data_parsers(sub)
    return p


def main() -> None:
    args = build_parser().parse_args()
    if local_commands.run(args):
        return
    if args.instance:
        local_commands.resolve_target(args)
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    main()
