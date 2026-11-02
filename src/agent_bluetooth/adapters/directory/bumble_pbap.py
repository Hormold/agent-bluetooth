"""Directory adapter: contacts + call history from the phone over PBAP.

PBAP pulls are slow (tens of seconds for a big phonebook), so reads never wait
on the phone: the last sync is kept on disk and served instantly. Syncs run in
the background when the phone connects, after each call (history), or on
request (POST /api/directory/sync)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ...core.context import Context
from ...core.events import E, Event
from ..bumble.pbap import BumblePbap
from ..bumble.radio import BumbleRadio

log = logging.getLogger(__name__)
KINDS = ("contacts", "history")


class BumblePbapDirectory:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.ctx = ctx
        self.path = Path(
            cfg.get("cache_file", "~/.agent-bluetooth/directory.json")
        ).expanduser()
        self.pbap: BumblePbap | None = None
        self.data: dict[str, Any] = {k: [] for k in KINDS}
        self.meta: dict[str, Any] = {
            "synced_at": {},
            "duration": {},
            "last_error": None,
            "phone": None,
        }
        self.syncing: str | None = None
        self.sync_timeout = float(cfg.get("sync_timeout", 120))
        # Phones allow one PBAP session at a time: serialize pulls.
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._load()
        self.ctx.bus.subscribe(self._on_event)
        radio = await BumbleRadio.shared(self.ctx)
        self.pbap = BumblePbap(radio)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # reads: always instant, from the last sync ---------------------------
    async def contacts(self) -> list[dict]:
        return await self._read("contacts")

    async def history(self) -> list[dict]:
        return await self._read("history")

    async def _read(self, kind: str) -> list[dict]:
        if kind not in self.meta["synced_at"]:
            # Never synced: this one request has to wait for the phone.
            await self._sync_kind(kind)
        return self.data[kind]

    def lookup(self, number: str) -> str | None:
        """Contact name for a number, from the last sync (last 10 digits match)."""
        key = _digits(number)
        if len(key) < 7:
            return None
        for c in self.data["contacts"]:
            if any(_digits(n) == key for n in c.get("numbers", [])):
                return c.get("name") or None
        return None

    def info(self) -> dict[str, Any]:
        return {
            "counts": {k: len(self.data[k]) for k in KINDS},
            "synced_at": self.meta["synced_at"],
            "duration": self.meta["duration"],
            "syncing": self.syncing,
            "last_error": self.meta["last_error"],
            "phone": self.meta["phone"],
            "cache_file": str(self.path),
        }

    # syncing --------------------------------------------------------------
    def sync(self, kinds: tuple[str, ...] = KINDS, delay: float = 0) -> None:
        """Start a background sync unless one is already running."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._sync_all(kinds, delay))

    async def _sync_all(self, kinds: tuple[str, ...], delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        for kind in kinds:
            try:
                await self._sync_kind(kind)
            except Exception as e:
                log.info("directory sync (%s) failed: %s", kind, e)

    async def _sync_kind(self, kind: str) -> None:
        async with self._lock:
            address = self._peer()
            if address is None or self.pbap is None:
                raise RuntimeError("phone not connected")
            fetch = self.pbap.contacts if kind == "contacts" else self.pbap.history
            self.syncing = kind
            started = time.time()
            try:
                items = [
                    x.to_dict()
                    for x in await asyncio.wait_for(fetch(address), self.sync_timeout)
                ]
            except Exception as e:
                self.meta["last_error"] = f"{kind}: {e or type(e).__name__}"
                raise
            finally:
                self.syncing = None
            self.data[kind] = items
            self.meta["synced_at"][kind] = time.time()
            self.meta["duration"][kind] = round(time.time() - started, 1)
            self.meta["phone"] = address
            self.meta["last_error"] = None
            self._save()
            log.info(
                "directory: %d %s synced in %.1fs",
                len(items),
                kind,
                self.meta["duration"][kind],
            )
            self.ctx.bus.publish(E.DIRECTORY_SYNCED, kind=kind, count=len(items))

    async def _on_event(self, event: Event) -> None:
        if event.type == E.PHONE_CONNECTED:
            self.sync(delay=5)  # let HFP/MAP settle first
        elif event.type == E.CALL_ENDED:
            self.sync(("history",), delay=3)

    def _peer(self) -> str | None:
        if self.pbap is None:
            return None
        for c in self.pbap.radio.device.connections.values():
            return str(c.peer_address)
        return None

    # persistence (private: it's someone's phonebook) -----------------------
    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except Exception as e:
            log.warning("directory cache unreadable (%s); starting empty", e)
            return
        for k in KINDS:
            self.data[k] = saved.get(k, [])
        self.meta.update(saved.get("meta", {}))
        self.meta["last_error"] = None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({**self.data, "meta": self.meta}, f)
        os.replace(tmp, self.path)


def _digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())[-10:]
