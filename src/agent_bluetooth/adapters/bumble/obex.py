"""Minimal OBEX client over an RFCOMM channel: CONNECT, SETPATH, GET, PUT.

Just enough for MAP (SMS) and PBAP (contacts). Spec: IrDA OBEX 1.5.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from ...core.tasks import Tasks

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from bumble import rfcomm

log = logging.getLogger(__name__)

# opcodes (final bit set)
CONNECT, PUT, GET, SETPATH = 0x80, 0x82, 0x83, 0x85
CONTINUE, OK = 0x90, 0xA0
# headers
NAME, TYPE, TARGET, BODY, END_OF_BODY, APP_PARAMS, CONNECTION_ID = (
    0x01,
    0x42,
    0x46,
    0x48,
    0x49,
    0x4C,
    0xCB,
)
# header identifier: the top two bits give the value encoding
_HI_KIND_MASK = 0xC0
_KIND_TEXT, _KIND_BYTES, _KIND_BYTE = 0x00, 0x40, 0x80  # else: 4-byte integer
_PREFIX_LEN = 3  # opcode/HI byte + 2-byte big-endian length, in packets and headers
_CONNECT_PREFIX_LEN = 7  # CONNECT reply adds version, flags, 2-byte max packet size
_BYTE_HEADER_LEN, _INT_HEADER_LEN = 2, 5  # HI + 1-byte value, HI + 4-byte value
_SETPATH_NO_CREATE, _SETPATH_PARENT = 0x02, 0x01
# What we tell the server we can receive (CONNECT), and the floor the spec puts
# on any side's maximum packet length (OBEX 1.5, 3.3.1.4).
MAX_RECEIVE = 0x7FFF
MIN_PACKET = 255


class ObexError(Exception):
    pass


class ObexProtocolError(ObexError):
    """The peer sent bytes that aren't valid OBEX. The session is unusable
    (we can't tell where the next packet starts) and gets torn down."""


def header(hi: int, value: str | bytes | int) -> bytes:
    kind = hi & _HI_KIND_MASK
    if kind == _KIND_TEXT:  # null-terminated UTF-16BE text; "" = empty header
        if not isinstance(value, str):
            raise TypeError(f"OBEX header 0x{hi:02x} takes text")
        data = (value.encode("utf-16-be") + b"\x00\x00") if value else b""
        return bytes([hi]) + (_PREFIX_LEN + len(data)).to_bytes(2, "big") + data
    if kind == _KIND_BYTES:
        if not isinstance(value, bytes):
            raise TypeError(f"OBEX header 0x{hi:02x} takes bytes")
        return bytes([hi]) + (_PREFIX_LEN + len(value)).to_bytes(2, "big") + value
    if not isinstance(value, int):
        raise TypeError(f"OBEX header 0x{hi:02x} takes an integer")
    if kind == _KIND_BYTE:
        return bytes([hi, value])
    return bytes([hi]) + value.to_bytes(4, "big")


def parse_headers(data: bytes) -> dict[int, bytes]:
    """Every header must fit in `data` exactly; a length that is too short
    (would never advance) or runs past the end raises ObexProtocolError."""
    out: dict[int, bytes] = {}
    i = 0
    while i < len(data):
        hi = data[i]
        kind = hi & _HI_KIND_MASK
        if kind in (_KIND_TEXT, _KIND_BYTES):
            if i + _PREFIX_LEN > len(data):
                raise ObexProtocolError(f"header 0x{hi:02x} truncated at offset {i}")
            length = int.from_bytes(data[i + 1 : i + 3], "big")
            start = i + _PREFIX_LEN
        else:
            length = _BYTE_HEADER_LEN if kind == _KIND_BYTE else _INT_HEADER_LEN
            start = i + 1
        if length < start - i or i + length > len(data):
            raise ObexProtocolError(
                f"header 0x{hi:02x} length {length} invalid at offset {i} of {len(data)}"
            )
        value, i = data[start : i + length], i + length
        out[hi] = out.get(hi, b"") + value
    return out


def app_params(**tags: tuple[int, int, int]) -> bytes:
    """app_params(max_count=(0x01, 2, 20)) -> tag, length, big-endian value."""
    return b"".join(
        bytes([tag, size]) + value.to_bytes(size, "big") for tag, size, value in tags.values()
    )


class ObexClient:
    def __init__(self, dlc: rfcomm.DLC, timeout: float = 15) -> None:
        self.dlc = dlc
        self.timeout = timeout
        self.connection_id: bytes = b""
        self.max_send = MIN_PACKET  # until CONNECT tells us the server's limit
        self.lock = asyncio.Lock()  # OBEX is strictly one request at a time
        self.failed: ObexError | None = None  # set once; the session is then dead
        self._buf = bytearray()
        self._packets: asyncio.Queue[bytes | ObexError] = asyncio.Queue()
        self._tasks = Tasks("obex")  # the channel close started from inside _on_data
        dlc.sink = self._on_data

    def _on_data(self, data: bytes) -> None:
        # Runs inside Bumble's RFCOMM receive path: never raise here, fail the
        # session instead (the waiting request sees the error).
        if self.failed is not None:
            return
        self._buf += data
        while len(self._buf) >= _PREFIX_LEN:
            length = int.from_bytes(self._buf[1:3], "big")
            if not _PREFIX_LEN <= length <= MAX_RECEIVE:
                self._fail(ObexProtocolError(f"packet length {length} out of range"))
                return
            if len(self._buf) < length:
                break
            self._packets.put_nowait(bytes(self._buf[:length]))
            del self._buf[:length]

    def _fail(self, error: ObexError) -> None:
        """Kill the session: the byte stream can't be trusted past this point.
        Pending and future requests raise `error`; the RFCOMM channel is closed."""
        if self.failed is not None:
            return
        log.warning("OBEX session failed: %s; closing the channel", error)
        self.failed = error
        self._buf.clear()
        self._packets.put_nowait(error)
        if (disconnect := getattr(self.dlc, "disconnect", None)) is not None:
            self._tasks.spawn(_quietly(disconnect()), name="obex-close")

    async def aclose(self) -> None:
        """End the session: stop a close already under way, then close the
        RFCOMM channel (quietly: it may be gone with the phone)."""
        await self._tasks.aclose()
        if (disconnect := getattr(self.dlc, "disconnect", None)) is not None:
            await _quietly(disconnect())

    async def _request(
        self, opcode: int, headers: bytes, extra: bytes = b""
    ) -> tuple[int, dict[int, bytes]]:
        if self.failed is not None:
            raise self.failed
        length = _PREFIX_LEN + len(extra) + len(headers)
        if length > self.max_send:
            raise ObexError(f"request of {length} bytes exceeds the server's {self.max_send}")
        self.dlc.write(bytes([opcode]) + length.to_bytes(2, "big") + extra + headers)
        try:
            packet = await asyncio.wait_for(self._packets.get(), self.timeout)
        except TimeoutError:
            # A late reply would be read as the answer to the next request.
            self._fail(ObexError(f"no reply to 0x{opcode:02x} within {self.timeout:g}s"))
            raise
        if isinstance(packet, ObexError):
            raise packet
        try:
            code, parsed, max_send = _parse_reply(opcode, packet)
        except ObexProtocolError as e:
            self._fail(e)
            raise
        if max_send is not None:
            self.max_send = max_send
        return code, parsed

    def _cid(self) -> bytes:
        return (
            header(CONNECTION_ID, int.from_bytes(self.connection_id, "big"))
            if self.connection_id
            else b""
        )

    async def connect(self, target: bytes) -> None:
        async with self.lock:
            extra = bytes([0x10, 0x00]) + MAX_RECEIVE.to_bytes(2, "big")  # OBEX 1.0, no flags
            code, h = await self._request(CONNECT, header(TARGET, target), extra)
            if code != OK:
                raise ObexError(f"CONNECT refused: 0x{code:02x}")
            self.connection_id = h.get(CONNECTION_ID, b"")

    async def setpath(self, name: str = "", *, up: bool = False) -> None:
        """name="" goes to root; up=True goes to the parent folder."""
        async with self.lock:
            flags = _SETPATH_NO_CREATE | (_SETPATH_PARENT if up else 0)
            headers = self._cid() + (b"" if up else header(NAME, name))
            code, _ = await self._request(SETPATH, headers, bytes([flags, 0]))
            if code != OK:
                raise ObexError(f"SETPATH {name!r} failed: 0x{code:02x}")

    async def get(self, type_: str, name: str | None = None, params: bytes = b"") -> bytes:
        async with self.lock:
            headers = self._cid() + header(TYPE, type_.encode() + b"\x00")
            if name is not None:
                headers += header(NAME, name)
            if params:
                headers += header(APP_PARAMS, params)
            body = b""
            while True:
                code, h = await self._request(GET, headers)
                body += h.get(BODY, b"") + h.get(END_OF_BODY, b"")
                if code == OK:
                    return body
                if code != CONTINUE:
                    raise ObexError(f"GET {type_} failed: 0x{code:02x}")
                headers = self._cid()

    async def put(
        self, type_: str, name: str, body: bytes, params: bytes = b""
    ) -> dict[int, bytes]:
        async with self.lock:
            headers = self._cid() + header(TYPE, type_.encode() + b"\x00") + header(NAME, name)
            if params:
                headers += header(APP_PARAMS, params)
            code, h = await self._request(PUT, headers + header(END_OF_BODY, body))
            if code != OK:
                raise ObexError(f"PUT {type_} failed: 0x{code:02x}")
            return h


def _parse_reply(opcode: int, packet: bytes) -> tuple[int, dict[int, bytes], int | None]:
    """(response code, headers, server max packet length if this was CONNECT)."""
    skip = _CONNECT_PREFIX_LEN if opcode == CONNECT else _PREFIX_LEN
    if len(packet) < skip:
        raise ObexProtocolError(f"reply of {len(packet)} bytes, need at least {skip}")
    max_send = None
    if opcode == CONNECT:  # bytes 5..6: the server's maximum packet length
        max_send = int.from_bytes(packet[5:7], "big")
        if max_send < MIN_PACKET:
            raise ObexProtocolError(f"server max packet {max_send} below the minimum {MIN_PACKET}")
    return packet[0], parse_headers(packet[skip:]), max_send


async def _quietly(closing: Awaitable[object]) -> None:
    with contextlib.suppress(Exception):  # the channel may already be gone
        await asyncio.wait_for(closing, 3)
