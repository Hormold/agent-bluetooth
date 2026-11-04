"""Talking to the daemon for the CLI: JSON over HTTP, the SSE stream, JSON output."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.client import HTTPResponse
from typing import TYPE_CHECKING, Any, NoReturn

import aiohttp

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterator

# A decoded JSON object from the daemon. Its shape is the daemon's API contract,
# not something the CLI can type more precisely.
JsonObject = dict[str, Any]

EXIT_INTERRUPTED = 130  # 128 + SIGINT, the shell convention
_SSE_DATA = "data:"
_SCHEMES = ("http", "https")


def out(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def fail(message: str, code: int = 1, **details: object) -> NoReturn:
    with contextlib.suppress(ValueError, AttributeError):
        message = json.loads(message).get("error", message)
    out({"error": message, **details})
    raise SystemExit(code)


def _url(args: argparse.Namespace, path: str) -> str:
    url = f"{args.host.rstrip('/')}{path}"
    if urllib.parse.urlsplit(url).scheme not in _SCHEMES:
        fail(f"--host must be an http(s) URL, got {args.host!r}", code=2)
    return url


def _build(
    args: argparse.Namespace,
    path: str,
    headers: dict[str, str],
    *,
    method: str | None = None,
    data: bytes | None = None,
) -> urllib.request.Request:
    if args.key:
        headers = {**headers, "Authorization": f"Bearer {args.key}"}
    url = _url(args, path)
    return urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 - _url allows http(s) only


def _open(req: urllib.request.Request, timeout: float | None = None) -> HTTPResponse:
    resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - built by _build, http(s) only
    if not isinstance(resp, HTTPResponse):  # urlopen is typed Any; http(s) gives this
        raise TypeError(f"unexpected response type {type(resp).__name__}")
    return resp


def request(
    args: argparse.Namespace, method: str, path: str, body: JsonObject | None = None
) -> object:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    try:
        req = _build(args, path, headers, method=method, data=data)
        with _open(req, args.timeout) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        fail(e.read().decode().strip(), status=e.code)
    except urllib.error.URLError as e:
        fail(f"Cannot reach daemon at {args.host}: {e.reason}. Start agent-bluetooth serve.")
    except (TimeoutError, OSError) as e:
        fail(f"Request to {args.host} failed: {e}")


def request_object(
    args: argparse.Namespace, method: str, path: str, body: JsonObject | None = None
) -> JsonObject:
    result = request(args, method, path, body)
    if not isinstance(result, dict):
        fail(f"Unexpected response from {path}: {result!r}")
    return result


def request_list(args: argparse.Namespace, path: str) -> list[JsonObject]:
    result = request(args, "GET", path)
    if not isinstance(result, list):
        fail(f"Unexpected response from {path}: {result!r}")
    return result


def _matches(event: JsonObject, filters: dict[str, str]) -> bool:
    for key, expected in filters.items():
        value = event if key.startswith("data.") or key in {"type", "ts"} else event["data"]
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return False
            value = value[part]
        actual = json.dumps(value) if not isinstance(value, str) else value
        if actual != expected:
            return False
    return True


def _sse_event(raw: bytes) -> JsonObject | None:
    line = raw.decode().strip()
    return json.loads(line[len(_SSE_DATA) :].strip()) if line.startswith(_SSE_DATA) else None


async def _wait(
    args: argparse.Namespace, event_type: str, filters: dict[str, str], seconds: float
) -> None:
    headers = {"Accept": "text/event-stream"}
    if args.key:
        headers["Authorization"] = f"Bearer {args.key}"
    url = _url(args, "/api/events")
    try:
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=seconds), headers=headers
            ) as client,
            client.get(url) as response,
        ):
            if response.status >= HTTPStatus.BAD_REQUEST:
                fail(await response.text(), status=response.status)
            async for raw in response.content:
                event = _sse_event(raw)
                if event is not None and event["type"] == event_type and _matches(event, filters):
                    out(event)
                    return
            fail("Event stream closed before a matching event arrived")
    except TimeoutError:
        fail(f"Timed out after {seconds:g}s waiting for {event_type}", code=1)
    except (aiohttp.ClientError, OSError) as e:
        fail(f"Cannot read events from {args.host}: {e}")


def wait_for(
    args: argparse.Namespace, event_type: str, filters: dict[str, str], timeout: float
) -> None:
    """Print the first live event of `event_type` matching `filters`; exit 1 on timeout."""
    try:
        asyncio.run(_wait(args, event_type, filters, timeout))
    except KeyboardInterrupt:
        fail("Interrupted", code=EXIT_INTERRUPTED)


def _events(args: argparse.Namespace) -> Iterator[JsonObject]:
    with _open(_build(args, "/api/events", {"Accept": "text/event-stream"})) as stream:
        for raw in stream:
            if (event := _sse_event(raw)) is not None:
                yield event


def watch(args: argparse.Namespace, families: set[str]) -> None:
    """Stream events as JSON lines until interrupted; empty `families` means all."""
    try:
        for event in _events(args):
            if not families or event["type"].split(".")[0] in families:
                sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    except urllib.error.URLError as e:
        fail(f"Cannot reach daemon at {args.host}: {e.reason}")
    except OSError as e:
        fail(f"Cannot read events from {args.host}: {e}")
    except KeyboardInterrupt:
        pass
