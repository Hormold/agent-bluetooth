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
from typing import TYPE_CHECKING, Any, TypeVar

from ...core.events import E
from ...core.tasks import Tasks
from ..bumble.pbap import BumblePbap, Contact, HistoryEntry
from ..bumble.radio import BumbleRadio

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ...core.context import Context
    from ...core.events import Event

log = logging.getLogger(__name__)
T = TypeVar("T")
KINDS = ("contacts", "history")
MIN_LOOKUP_DIGITS = 7  # shorter numbers (short codes) match too much to name a contact


class BumblePbapDirectory:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.ctx = ctx
        self.path = Path(cfg["cache_file"]).expanduser()
        self.pbap: BumblePbap | None = None
        self.data: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
        self._names: dict[str, str] = {}  # last 10 digits -> contact name, from data["contacts"]
        self._seen: dict[str, str] = {}  # last 10 digits -> name the phone showed in history
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
        self._tasks = Tasks(f"{ctx.bus.instance}:directory")
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._load()
        self.ctx.bus.subscribe(self._on_event)
        radio = await BumbleRadio.shared(self.ctx)
        self.pbap = BumblePbap(radio)

    async def stop(self) -> None:
        self._task = None
        await self._tasks.aclose()

    # reads: always instant, from the last sync ---------------------------
    async def contacts(self) -> list[dict[str, Any]]:
        return await self._read("contacts")

    async def history(self) -> list[dict[str, Any]]:
        return await self._read("history")

    async def _read(self, kind: str) -> list[dict[str, Any]]:
        if kind not in self.meta["synced_at"]:
            # Never synced: this one request has to wait for the phone.
            await self._sync_kind(kind)
        return self.data[kind]

    def lookup(self, number: str) -> str | None:
        """Contact name for a number, from the last sync (last 10 digits match)."""
        key = _digits(number)
        if len(key) < MIN_LOOKUP_DIGITS:
            return None
        return self._names.get(key) or None

    def history_name(self, number: str) -> str | None:
        """Name the phone showed for this number in call history (e.g. a business
        caller ID from Maps), for numbers that aren't contacts."""
        key = _digits(number)
        if len(key) < MIN_LOOKUP_DIGITS:
            return None
        return self._seen.get(key) or None

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
        self._task = self._tasks.spawn(self._sync_all(kinds, delay), name="directory-sync")

    async def _sync_all(self, kinds: tuple[str, ...], delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        for kind in kinds:
            try:
                await self._sync_kind(kind)
            except (RuntimeError, TimeoutError) as e:  # phone gone or too slow: expected
                log.info("directory sync (%s) failed: %s", kind, e or type(e).__name__)
            except Exception:  # background task: one bad pull must not kill the rest
                log.warning("directory sync (%s) failed", kind, exc_info=True)

    async def _sync_kind(self, kind: str) -> None:
        async with self._lock:
            address = self._peer()
            if address is None or self.pbap is None:
                raise RuntimeError("phone not connected")
            fetch = self.pbap.contacts if kind == "contacts" else self.pbap.history
            self.syncing = kind
            started = time.time()
            try:
                pulled = await asyncio.wait_for(fetch(address), self.sync_timeout)
            except Exception as e:
                self.meta["last_error"] = f"{kind}: {e or type(e).__name__}"
                raise
            finally:
                self.syncing = None
            self._keep(kind, pulled)
            items = self.data[kind]
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
        except (OSError, ValueError) as e:  # ValueError covers bad JSON and bad UTF-8
            log.warning("directory cache unreadable (%s); starting empty", e)
            return
        if not isinstance(saved, dict):
            log.warning("directory cache malformed; starting empty")
            return
        # Our own file, but still outside data: keep only well-formed entries.
        self._keep("contacts", _entries(saved.get("contacts"), _contact))
        self._keep("history", _entries(saved.get("history"), _history))
        self.meta.update(saved.get("meta", {}))
        self.meta["last_error"] = None

    def _keep(self, kind: str, items: Sequence[Contact | HistoryEntry]) -> None:
        self.data[kind] = [x.to_dict() for x in items]
        if kind == "contacts":
            self._names = {
                _digits(n): c.name for c in items if isinstance(c, Contact) for n in c.numbers
            }
        else:
            # newest first from the phone; keep the first name seen per number
            seen: dict[str, str] = {}
            for h in items:
                if isinstance(h, HistoryEntry) and h.name and _digits(h.number):
                    seen.setdefault(_digits(h.number), h.name)
            self._seen = seen

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({**self.data, "meta": self.meta}, f)
        tmp.replace(self.path)


def _digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())[-10:]


def _entries(raw: object, parse: Callable[[object], T | None]) -> list[T]:
    if not isinstance(raw, list):
        return []
    return [x for x in map(parse, raw) if x is not None]


def _contact(raw: object) -> Contact | None:
    if not isinstance(raw, dict):
        return None
    name, numbers = raw.get("name"), raw.get("numbers")
    if not isinstance(name, str) or not isinstance(numbers, list):
        return None
    return Contact(name=name, numbers=[n for n in numbers if isinstance(n, str)])


def _history(raw: object) -> HistoryEntry | None:
    if not isinstance(raw, dict):
        return None
    name, number, kind, at = (raw.get(k) for k in ("name", "number", "kind", "at"))
    if not (
        isinstance(name, str)
        and isinstance(number, str)
        and isinstance(kind, str)
        and isinstance(at, str)
    ):
        return None
    return HistoryEntry(name=name, number=number, kind=kind, at=at)
