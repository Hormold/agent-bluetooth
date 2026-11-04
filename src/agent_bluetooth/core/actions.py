"""Action handlers for the automation engine. Each is `async (params, vars)`.

Actions reuse the adapters already wired in the app — no new execution paths.
`params` are the automation table's string fields, already templated.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from .ports import ActionHost, Call

log = logging.getLogger(__name__)
Action = Callable[[dict[str, Any], dict[str, str]], Awaitable[None]]
HTTP_ERROR = 400  # first status code that means the request failed


def build_actions(app: ActionHost) -> dict[str, Action]:
    a = Actions(app)
    return {
        "log": a.log,
        "shell": a.shell,
        "webhook": a.webhook,
        "telegram": a.telegram,
        "sms": a.sms,  # auto-reply: `to` defaults to the sender/number
        "hangup": a.hangup,
        "answer": a.answer,
        "play": a.play,  # needs `file`
        "bridge": a.bridge,
    }


class Actions:
    def __init__(self, app: ActionHost) -> None:
        self.app = app

    async def log(self, p: dict[str, Any], v: dict[str, str]) -> None:
        # stdout on purpose: the operator's terminal, apart from the log stream.
        sys.stdout.write(f"{p.get('text', str(v))}\n")
        sys.stdout.flush()

    async def shell(self, p: dict[str, Any], v: dict[str, str]) -> None:
        # vars exposed as AB_* env so scripts don't need to parse anything.
        env = {f"AB_{k.upper()}": val for k, val in v.items()}
        proc = await asyncio.create_subprocess_shell(p["cmd"], env={**os.environ, **env})
        try:
            await proc.wait()
        except asyncio.CancelledError:  # the daemon is stopping: don't leave the script behind
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()  # reap it, or the transport outlives the loop
            raise

    async def webhook(self, p: dict[str, Any], v: dict[str, str]) -> None:
        body = {"vars": v} if "text" not in p else {"text": p["text"], "vars": v}
        async with aiohttp.ClientSession() as http, http.post(p["url"], json=body) as r:
            if r.status >= HTTP_ERROR:
                log.warning("webhook %s -> %s", p["url"], r.status)

    async def telegram(self, p: dict[str, Any], _v: dict[str, str]) -> None:
        cfg = self._sink_cfg("telegram")
        token = p.get("bot_token") or cfg.get("bot_token")
        chat = p.get("chat_id") or cfg.get("chat_id")
        if not token or not chat:
            raise RuntimeError(
                "telegram action needs bot_token+chat_id (here or in a telegram sink)"
            )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": str(chat),
            "text": p.get("text", ""),
            "disable_web_page_preview": True,
        }
        async with aiohttp.ClientSession() as http, http.post(url, json=payload) as r:
            if r.status >= HTTP_ERROR:
                log.warning("telegram -> %s %s", r.status, await r.text())

    async def sms(self, p: dict[str, Any], v: dict[str, str]) -> None:
        if self.app.messages is None:
            raise RuntimeError("sms action needs a messages adapter")
        to = p.get("to") or v.get("sender") or v.get("number")
        if not to:
            raise RuntimeError("sms action: no `to` and no sender/number in event")
        await self.app.messages.send(to, p["text"])

    async def hangup(self, _p: dict[str, Any], _v: dict[str, str]) -> None:
        await self.app.phone.hangup()

    async def answer(self, _p: dict[str, Any], _v: dict[str, str]) -> None:
        await self.app.phone.answer()

    async def play(self, p: dict[str, Any], v: dict[str, str]) -> None:
        call = self._call(v)
        await self.app.router.play_file(
            call,
            p["file"],
            loop=as_bool(p.get("loop")),
            hangup_after=as_bool(p.get("hangup_after"), default=True),
        )

    async def bridge(self, _p: dict[str, Any], v: dict[str, str]) -> None:
        await self.app.router.route_to_room(self._call(v))

    # helpers ---------------------------------------------------------------
    def _call(self, v: dict[str, str]) -> Call:
        cid = v.get("id") or v.get("call_id")
        calls = self.app.router.calls
        if cid and cid in calls:
            return calls[cid]
        if len(calls) == 1:
            return next(iter(calls.values()))
        raise RuntimeError("no single active call for this action")

    def _sink_cfg(self, adapter: str) -> dict[str, Any]:
        for s in self.app.config.get("sinks", []):
            if s.get("adapter") == adapter:
                return s
        return {}


def as_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes", "on")
