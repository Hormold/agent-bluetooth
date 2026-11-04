"""Routing rules: which rule (if any) decides a call. First match wins."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ports import Call


def rule_for(rules: list[dict[str, Any]], call: Call) -> dict[str, Any] | None:
    """First matching rule. Conditions (all optional): direction in|out|any,
    when_number (glob on the number), when_contact (true/false)."""
    for rule in rules:
        direction = rule.get("direction", "in")
        if direction not in ("any", call.direction):
            continue
        pattern = rule.get("when_number")
        if pattern and not number_matches(call.number or "", pattern):
            continue
        if "when_contact" in rule and bool(rule["when_contact"]) != call.contact:
            continue
        return rule
    return None


def rules_need_caller(rules: list[dict[str, Any]]) -> bool:
    """Some rule looks at who calls, so routing must wait for caller id."""
    return any("when_number" in r or "when_contact" in r for r in rules)


def number_matches(number: str, pattern: str) -> bool:
    """Glob patterns (+1800*) match the raw number; a plain number matches on
    digits, so "+1 (415) 555-0142" and "4155550142" are the same caller."""
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(number, pattern)
    a = "".join(c for c in number if c.isdigit())[-10:]
    b = "".join(c for c in pattern if c.isdigit())[-10:]
    return bool(a) and a == b
