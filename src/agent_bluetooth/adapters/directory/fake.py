"""Directory from config, for demos and integration runs without a phone.

[directory]
adapter = "fake"
contacts = [{ name = "Anna Petrova", numbers = ["+14155550142"] }]
"""

from __future__ import annotations

from typing import Any

from ...core.context import Context


class FakeDirectory:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self._contacts = [
            {"name": c["name"], "numbers": list(c.get("numbers", []))}
            for c in cfg.get("contacts", [])
        ]

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def contacts(self) -> list[dict]:
        return self._contacts

    async def history(self) -> list[dict]:
        return []

    def lookup(self, number: str) -> str | None:
        key = _digits(number)
        if len(key) < 7:
            return None
        for c in self._contacts:
            if any(_digits(n) == key for n in c["numbers"]):
                return c["name"]
        return None

    def info(self) -> dict[str, Any]:
        return {
            "counts": {"contacts": len(self._contacts), "history": 0},
            "synced_at": {},
            "duration": {},
            "syncing": None,
            "last_error": None,
            "phone": None,
        }

    def sync(self, kinds: tuple[str, ...] = (), delay: float = 0) -> None: ...


def _digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())[-10:]
