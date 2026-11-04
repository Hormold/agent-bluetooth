"""SMS over Bluetooth MAP (Message Access Profile), the way car kits do it.

Works with Android (phone asks once: "allow access to messages?") and with
iPhone (turn on "Show Notifications" for the device in Bluetooth settings).
Inbound: polls the inbox listing every few seconds (no MNS server needed).
Outbound: pushes a bMessage to the outbox; iOS may refuse, Android accepts.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

from bumble import rfcomm
from bumble.core import UUID

from ...core.events import E
from ...core.tasks import Tasks
from ..bumble.obex import ObexClient, app_params
from ..bumble.radio import BumbleRadio

if TYPE_CHECKING:
    from ...core.context import Context
    from ...core.events import Event

log = logging.getLogger(__name__)
SEEN_MAX = 5000  # message handles remembered to skip already-delivered SMS
MAS_UUID = UUID.from_16_bits(0x1132)
MAS_TARGET = bytes.fromhex("bb582b40420c11dbb0de0800200c9a66")
T_MAX_COUNT, T_SUBJECT_LEN, T_ATTACHMENT, T_CHARSET = 0x01, 0x13, 0x0A, 0x14


class BumbleMapMessages:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.ctx = ctx
        self.bus = ctx.bus
        self.poll_every = float(cfg.get("poll_every", 5))
        # Fetching the full message marks it read on most phones; the listing
        # subject already holds the text for normal-length SMS.
        self.fetch_full = bool(cfg.get("fetch_full", False))
        self.obex: ObexClient | None = None
        self._tasks = Tasks(f"{ctx.bus.instance}:map")
        self._task: asyncio.Task[None] | None = None  # the session with the connected phone
        self._seen: dict[str, None] = {}  # insertion-ordered set, capped at SEEN_MAX

    async def start(self) -> None:
        self.radio = await BumbleRadio.shared(self.ctx)
        self.bus.subscribe(self._on_event)

    async def stop(self) -> None:
        self._task = None
        await self._tasks.aclose()

    async def _on_event(self, event: Event) -> None:
        if event.type == E.PHONE_CONNECTED and self._task is None:
            address = str(event.data["address"])
            self._task = self._tasks.spawn(self._run(address), name=f"map:{address}")
        elif event.type == E.PHONE_DISCONNECTED and self._task:
            self._task.cancel()
            self._task = None

    async def send(self, to: str, text: str) -> None:
        if self.obex is None or self.obex.failed is not None:
            raise RuntimeError("MAP not connected")
        await self.obex.put(
            "x-bt/message",
            "outbox",
            _bmessage(to, text),
            app_params(c=(T_CHARSET, 1, 1)),
        )
        self.bus.publish(E.SMS_SENT, to=to, text=text)

    # internals -------------------------------------------------------------
    async def _run(self, address: str) -> None:
        """The MAP session with one connected phone, until it disconnects."""
        try:
            obex = await self._open(address)
        except Exception:
            log.exception("MAP connect failed; SMS disabled until the phone reconnects")
            self._forget_session()
            return
        if obex is None:
            return
        self.obex = obex
        log.info("MAP connected, watching inbox")
        try:
            await self._watch(obex)
        except Exception:
            log.exception("MAP session failed; SMS disabled until the phone reconnects")
            self._forget_session()
        finally:
            if self.obex is obex:  # a newer session may already be up
                self.obex = None
            await obex.aclose()

    async def _open(self, address: str) -> ObexClient | None:
        conn = next(
            c for c in self.radio.device.connections.values() if str(c.peer_address) == address
        )
        channel = await rfcomm.find_rfcomm_channel_with_uuid(conn, MAS_UUID)
        if channel is None:
            log.warning("phone has no MAP service; SMS disabled")
            return None
        mux = await self.radio.multiplexer(conn)
        obex = ObexClient(await mux.open_dlc(channel))
        try:
            await obex.connect(MAS_TARGET)
            for folder in ("", "telecom", "msg"):
                await obex.setpath(folder)
        except BaseException:
            await obex.aclose()
            raise
        return obex

    async def _watch(self, obex: ObexClient) -> None:
        first = True
        while True:
            await self._poll(obex, seed=first)
            first = False
            await asyncio.sleep(self.poll_every)

    def _forget_session(self) -> None:
        """The session died on its own: the next PHONE_CONNECTED starts a new one."""
        if self._task is asyncio.current_task():
            self._task = None

    async def _poll(self, obex: ObexClient, *, seed: bool) -> None:
        params = app_params(n=(T_MAX_COUNT, 2, 20), s=(T_SUBJECT_LEN, 1, 255))
        listing = await obex.get("x-bt/MAP-msg-listing", "inbox", params)
        # The phone is the only source; expat >= 2.4.1 (3.11 ships 2.6) blocks
        # entity-expansion bombs and ElementTree never fetches external entities.
        root = ET.fromstring(listing)  # noqa: S314 - see above; defusedxml isn't a dependency
        for msg in root.iter("msg"):
            handle = msg.get("handle", "")
            if handle in self._seen:
                continue
            self._seen[handle] = None
            if len(self._seen) > SEEN_MAX:  # a session can run for weeks; forget the oldest
                del self._seen[next(iter(self._seen))]
            if seed:  # don't replay the whole inbox on startup
                continue
            text = msg.get("subject", "")
            if self.fetch_full:
                text = await self._fetch(obex, handle) or text
            self.bus.publish(
                E.SMS_RECEIVED,
                id=handle,
                sender=msg.get("sender_addressing", ""),
                name=msg.get("sender_name", ""),
                text=text,
                datetime=msg.get("datetime", ""),
                kind=msg.get("type", ""),
            )

    @staticmethod
    async def _fetch(obex: ObexClient, handle: str) -> str:
        raw = await obex.get(
            "x-bt/message",
            handle,
            app_params(a=(T_ATTACHMENT, 1, 0), c=(T_CHARSET, 1, 1)),
        )
        text = raw.decode("utf-8", "replace")
        start, end = text.find("BEGIN:MSG"), text.rfind("END:MSG")
        return text[start + len("BEGIN:MSG") : end].strip() if start >= 0 < end else ""


def _bmessage(to: str, text: str) -> bytes:
    msg = f"BEGIN:MSG\r\n{text}\r\nEND:MSG\r\n"
    return (
        "BEGIN:BMSG\r\nVERSION:1.0\r\nSTATUS:READ\r\nTYPE:SMS_GSM\r\nFOLDER:telecom/msg/outbox\r\n"
        "BEGIN:BENV\r\n"
        f"BEGIN:VCARD\r\nVERSION:2.1\r\nN:{to}\r\nTEL:{to}\r\nEND:VCARD\r\n"
        f"BEGIN:BBODY\r\nCHARSET:UTF-8\r\nLENGTH:{len(msg.encode())}\r\n{msg}END:BBODY\r\n"
        "END:BENV\r\nEND:BMSG\r\n"
    ).encode()
