"""Directory from config, for demos and integration runs without a phone.

[directory]
adapter = "fake"
contacts = [{ name = "Anna Petrova", numbers = ["+14155550142"] }]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.context import Context

MIN_LOOKUP_DIGITS = 7  # same rule as the PBAP directory: short codes never match


class FakeDirectory:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self._contacts = [_contact(i, c) for i, c in enumerate(cfg.get("contacts", []))]

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def contacts(self) -> list[dict[str, Any]]:
        return [{"name": name, "numbers": list(numbers)} for name, numbers in self._contacts]

    async def history(self) -> list[dict[str, Any]]:
        return []

    def lookup(self, number: str) -> str | None:
        key = _digits(number)
        if len(key) < MIN_LOOKUP_DIGITS:
            return None
        for name, numbers in self._contacts:
            if any(_digits(n) == key for n in numbers):
                return name
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


def _contact(i: int, raw: object) -> tuple[str, tuple[str, ...]]:
    """One `contacts` entry from the config, checked: a name and its numbers."""
    if not isinstance(raw, dict):
        raise TypeError(f"directory.contacts[{i}] must be a table")
    name, numbers = raw.get("name"), raw.get("numbers", [])
    if not isinstance(name, str) or not isinstance(numbers, list):
        raise TypeError(f"directory.contacts[{i}] needs a name and a list of numbers")
    checked = tuple(n for n in numbers if isinstance(n, str))
    if len(checked) != len(numbers):
        raise TypeError(f"directory.contacts[{i}].numbers must be strings")
    return name, checked
