"""Probe: feed malformed OBEX bytes to the real parser and client, show each is
rejected promptly with ObexProtocolError and the session is torn down.

    uv run python scripts/scenarios/obex_malformed.py

The client runs over a stand-in RFCOMM channel (the only thing faked: no phone
needed). Every case has a hard 2 s wall-clock alarm, so a parser that loops
forever fails the probe instead of hanging it. Exit code 0 = all rejected.
"""

# ruff: noqa: T201, INP001 - a standalone probe that reports on stdout, not a package module

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from typing import TYPE_CHECKING

from agent_bluetooth.adapters.bumble.obex import (
    CONNECT,
    GET,
    ObexClient,
    ObexProtocolError,
    parse_headers,
)

if TYPE_CHECKING:
    from collections.abc import Callable

HANG_S = 2


class HungError(Exception):
    pass


def _alarm(*_: object) -> None:
    raise HungError(f"no answer within {HANG_S}s (parser loops or waits forever)")


class Channel:
    """Stands in for bumble.rfcomm.DLC: replies with scripted bytes."""

    def __init__(self, replies: list[bytes]) -> None:
        self.sink: Callable[[bytes], None] | None = None
        self.replies = replies
        self.closed = False

    def write(self, _data: bytes) -> None:
        if self.replies and self.sink:
            asyncio.get_running_loop().call_soon(self.sink, self.replies.pop(0))

    async def disconnect(self) -> None:
        self.closed = True


async def _client(replies: list[bytes], ops: list[int]) -> Channel:
    dlc = Channel(replies)
    client = ObexClient(dlc, timeout=HANG_S)  # type: ignore[arg-type]  # stand-in channel
    try:
        for op in ops:
            await client._request(op, b"")  # noqa: SLF001 - probing the wire layer directly
    finally:
        await asyncio.sleep(0)  # let the close task run
    # A failed session must refuse further use without touching the wire.
    with contextlib.suppress(ObexProtocolError):
        await client._request(GET, b"")  # noqa: SLF001
    return dlc


HEADERS = {
    "zero-length text header": b"\x01\x00\x00",
    "short bytes header (len 1)": b"\x48\x00\x01xyz",
    "bytes header longer than packet": b"\x48\x00\xffabc",
    "header prefix cut off": b"\x48\x00",
    "truncated 4-byte header": b"\xcb\x00\x01",
    "truncated 1-byte header": b"\x93",
}
PACKETS = {
    "zero-length packet": ([b"\xa0\x00\x00"], [GET]),
    "packet length 2 (< prefix)": ([b"\xa0\x00\x02"], [GET]),
    "packet over our max receive": ([b"\xa0\xff\xff" + b"\x00" * 16], [GET]),
    "CONNECT reply without max size": ([b"\xa0\x00\x03"], [CONNECT]),
    "CONNECT max packet below 255": ([b"\xa0\x00\x07\x10\x00\x00\x10"], [CONNECT]),
    "bad header inside a valid GET": ([b"\xa0\x00\x06\x01\x00\x00"], [GET]),
}


def _run(name: str, probe: Callable[[], object]) -> bool:
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(HANG_S)
    t0 = time.monotonic()
    try:
        result = probe()
    except ObexProtocolError as e:
        ms = (time.monotonic() - t0) * 1000
        print(f"ok    {name:34} rejected in {ms:5.1f} ms: {e}")
        return True
    except Exception as e:  # noqa: BLE001 - any other outcome (hang, crash) is the finding
        print(f"FAIL  {name:34} {type(e).__name__}: {e}")
        return False
    finally:
        signal.alarm(0)
    print(f"FAIL  {name:34} accepted: {result!r}")
    return False


def _packet_probe(replies: list[bytes], ops: list[int]) -> Callable[[], object]:
    def probe() -> object:
        dlc = asyncio.run(_client(list(replies), ops))
        return f"session survived, channel closed={dlc.closed}"

    return probe


def _closes_channel() -> bool:
    async def go() -> bool:
        dlc = Channel([b"\xa0\x00\x00"])
        client = ObexClient(dlc, timeout=HANG_S)  # type: ignore[arg-type]
        try:
            await client._request(GET, b"")  # noqa: SLF001
        except Exception:  # noqa: BLE001 - only whether the channel got closed matters here
            await asyncio.sleep(0.05)
        return dlc.closed

    closed = asyncio.run(go())
    print(f"{'ok  ' if closed else 'FAIL'}  malformed input closes the RFCOMM channel: {closed}")
    return closed


def main() -> int:
    results = [_run(n, lambda d=d: parse_headers(d)) for n, d in HEADERS.items()]
    results += [_run(n, _packet_probe(*case)) for n, case in PACKETS.items()]
    results.append(_closes_channel())
    print(f"{sum(results)}/{len(results)} malformed inputs rejected")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
