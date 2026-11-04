"""One bounded JSON POST for the network sinks.

Sinks run as fire-and-forget bus tasks, so a dead endpoint never blocks the
loop; the timeout keeps a hanging one from piling up tasks and sockets, and
failures are logged as one line instead of a traceback per event."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import aiohttp

log = logging.getLogger(__name__)
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_LIMIT = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_S)
_ERROR_BODY_CHARS = 500


def http_url(url: str, what: str) -> str:
    """The URL, if it is http(s); fail at startup on anything else (file:, typos)."""
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError(f"{what}: url must be http:// or https://, got {url!r}")
    return url


async def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    what: str,
    headers: dict[str, str] | None = None,
    limit: aiohttp.ClientTimeout = DEFAULT_LIMIT,
) -> None:
    try:
        async with (
            aiohttp.ClientSession(timeout=limit) as http,
            http.post(url, json=payload, headers=headers) as r,
        ):
            if not r.ok:
                body = (await r.text())[:_ERROR_BODY_CHARS]
                log.warning("%s -> %s %s", what, r.status, body)
    except TimeoutError:
        log.warning("%s: no response in %.0fs", what, limit.total)
    except aiohttp.ClientError as e:
        log.warning("%s failed: %s", what, e or type(e).__name__)
