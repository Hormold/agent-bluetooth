"""Forwards SMS and call notifications to a Telegram chat."""

from __future__ import annotations

import logging
from html import escape
from typing import Any

import aiohttp

from ...core.context import Context
from ...core.events import E, Event

log = logging.getLogger(__name__)


def _who(d: dict[str, Any]) -> str:
    name, number = d.get("name") or "", d.get("number") or d.get("sender") or "unknown"
    return escape(f"{name} ({number})" if name else number)


class TelegramSink:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.token: str = cfg["bot_token"]
        self.chat_id: str = str(cfg["chat_id"])
        self.web_url: str = cfg.get("web_url", "").rstrip("/")  # adds a "join" link

    def render(self, event: Event) -> str | None:
        d = event.data
        call = d.get("call", {})
        match event.type:
            case E.SMS_RECEIVED:
                return f"💬 <b>SMS from {_who(d)}</b>\n{escape(d.get('text', ''))}"
            case E.ROOM_OPENED if d.get("direction") == "in":
                link = f'\n<a href="{self.web_url}/?room={d["room"]}">Join the call</a>' if self.web_url else ""
                return f"📞 <b>Incoming call</b> from {_who(d)}\nRoom: <code>{escape(d['room'])}</code>{link}"
            case E.CALL_ENDED:
                return f"☎️ Call with {_who(call)} ended"
            case E.PHONE_CONNECTED:
                return f"🔗 Phone connected: {escape(d.get('name', ''))}"
            case E.PHONE_DISCONNECTED:
                return "⛓️‍💥 Phone disconnected"
        return None

    async def handle(self, event: Event) -> None:
        text = self.render(event)
        if text is None:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        async with aiohttp.ClientSession() as http:
            async with http.post(url, json=payload) as r:
                if r.status >= 400:
                    log.warning("telegram %s %s", r.status, await r.text())
