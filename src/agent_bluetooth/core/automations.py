"""Automations: declarative "on event X, do Y" with templating.

This is the no-agent trigger layer. Each rule matches an event by type + field
globs, optionally pulls variables out with a regex, then runs one action whose
params are templated with {{var}}. Everyday recipes (OTP relay, notification
forward, call blocking, driving auto-reply) become pure config.

    [[automations]]
    on = "sms.received"
    when = { text = "*code*" }
    extract = { code = "(\\d{4,8})" }      # literal regex, NOT semantic parsing
    do = "telegram"
    text = "🔐 {{code}} from {{sender}}"

Matching is glob/regex only. Anything needing meaning is handed to an agent.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .context import Context
from .events import Event

log = logging.getLogger(__name__)
Action = Callable[[dict[str, Any], dict[str, str]], Awaitable[None]]
_TEMPLATE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class AutomationEngine:
    def __init__(self, ctx: Context, actions: dict[str, Action]) -> None:
        self.rules: list[dict[str, Any]] = ctx.config.get("automations", [])
        self.actions = actions
        for rule in self.rules:
            if rule.get("do") not in actions:
                log.warning("automation %r: unknown action %r", rule.get("name", "?"), rule.get("do"))
        if self.rules:
            ctx.bus.subscribe(self.on_event)
            log.info("loaded %d automations", len(self.rules))

    async def on_event(self, event: Event) -> None:
        fields = _flatten(event)
        for rule in self.rules:
            if not fnmatch.fnmatch(event.type, rule["on"]):
                continue
            if not _matches(rule.get("when", {}), fields):
                continue
            extracted = _extract(rule.get("extract", {}), fields)
            if extracted is None:  # a required pattern didn't match -> skip
                continue
            action = self.actions.get(rule["do"])
            if action is None:
                continue
            ctx_vars = {**fields, **extracted}
            params = {k: _render(v, ctx_vars) for k, v in rule.items() if isinstance(v, str)}
            log.info("automation %r fired on %s", rule.get("name", rule["do"]), event.type)
            asyncio.create_task(self._run(rule.get("name", rule["do"]), action, params, ctx_vars))

    async def _run(self, name: str, action: Action, params: dict[str, Any], ctx_vars: dict[str, str]) -> None:
        try:
            await action(params, ctx_vars)
        except Exception:
            log.exception("automation %r failed", name)


def _flatten(event: Event) -> dict[str, str]:
    """A flat string view of the event for matching/templating. Call sub-dict is
    hoisted so `number`, `name`, `state` are top-level."""
    out: dict[str, str] = {"event": event.type}
    data = dict(event.data)
    for key in ("call",):
        if isinstance(data.get(key), dict):
            for k, v in data.pop(key).items():
                out[k] = "" if v is None else str(v)
    for k, v in data.items():
        out[k] = "" if v is None else str(v)
    return out


def _matches(when: dict[str, Any], fields: dict[str, str]) -> bool:
    return all(fnmatch.fnmatch(fields.get(k, ""), str(pat)) for k, pat in when.items())


def _extract(spec: dict[str, str], fields: dict[str, str]) -> dict[str, str] | None:
    """Run each regex against the `text` field (fallback: all fields joined).
    First capture group -> variable. Missing match aborts the rule."""
    if not spec:
        return {}
    haystack = fields.get("text") or " ".join(fields.values())
    out: dict[str, str] = {}
    for var, pattern in spec.items():
        m = re.search(pattern, haystack)
        if m is None:
            return None
        out[var] = m.group(1) if m.groups() else m.group(0)
    return out


def _render(template: str, ctx_vars: dict[str, str]) -> str:
    return _TEMPLATE.sub(lambda m: ctx_vars.get(m[1], m[0]), template)
