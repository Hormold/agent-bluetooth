"""Minimal OBEX client over an RFCOMM channel: CONNECT, SETPATH, GET, PUT.

Just enough for MAP (SMS) and PBAP (contacts). Spec: IrDA OBEX 1.5.
"""

from __future__ import annotations

import asyncio
from typing import Any

from bumble import rfcomm

# opcodes (final bit set)
CONNECT, PUT, GET, SETPATH = 0x80, 0x82, 0x83, 0x85
CONTINUE, OK = 0x90, 0xA0
# headers
NAME, TYPE, TARGET, BODY, END_OF_BODY, APP_PARAMS, CONNECTION_ID = 0x01, 0x42, 0x46, 0x48, 0x49, 0x4C, 0xCB


class ObexError(Exception):
    pass


def header(hi: int, value: Any) -> bytes:
    kind = hi & 0xC0
    if kind == 0x00:  # null-terminated UTF-16BE text; "" = empty header
        data = (value.encode("utf-16-be") + b"\x00\x00") if value else b""
        return bytes([hi]) + (3 + len(data)).to_bytes(2, "big") + data
    if kind == 0x40:  # byte sequence
        return bytes([hi]) + (3 + len(value)).to_bytes(2, "big") + value
    if kind == 0x80:
        return bytes([hi, value])
    return bytes([hi]) + int(value).to_bytes(4, "big")


def parse_headers(data: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    i = 0
    while i < len(data):
        hi = data[i]
        kind = hi & 0xC0
        if kind in (0x00, 0x40):
            length = int.from_bytes(data[i + 1 : i + 3], "big")
            value, i = data[i + 3 : i + length], i + length
        elif kind == 0x80:
            value, i = data[i + 1 : i + 2], i + 2
        else:
            value, i = data[i + 1 : i + 5], i + 5
        out[hi] = out.get(hi, b"") + value
    return out


def app_params(**tags: tuple[int, int, int]) -> bytes:
    """app_params(max_count=(0x01, 2, 20)) -> tag, length, big-endian value."""
    return b"".join(bytes([tag, size]) + value.to_bytes(size, "big") for tag, size, value in tags.values())


class ObexClient:
    def __init__(self, dlc: rfcomm.DLC, timeout: float = 15) -> None:
        self.dlc = dlc
        self.timeout = timeout
        self.connection_id: bytes = b""
        self.lock = asyncio.Lock()  # OBEX is strictly one request at a time
        self._buf = bytearray()
        self._packets: asyncio.Queue[bytes] = asyncio.Queue()
        dlc.sink = self._on_data

    def _on_data(self, data: bytes) -> None:
        self._buf += data
        while len(self._buf) >= 3:
            length = int.from_bytes(self._buf[1:3], "big")
            if len(self._buf) < length:
                break
            self._packets.put_nowait(bytes(self._buf[:length]))
            del self._buf[:length]

    async def _request(self, opcode: int, headers: bytes, extra: bytes = b"") -> tuple[int, dict[int, bytes]]:
        length = 3 + len(extra) + len(headers)
        self.dlc.write(bytes([opcode]) + length.to_bytes(2, "big") + extra + headers)
        packet = await asyncio.wait_for(self._packets.get(), self.timeout)
        skip = 7 if opcode == CONNECT else 3  # connect reply carries version/flags/max-size
        return packet[0], parse_headers(packet[skip:])

    def _cid(self) -> bytes:
        return header(CONNECTION_ID, int.from_bytes(self.connection_id, "big")) if self.connection_id else b""

    async def connect(self, target: bytes) -> None:
        async with self.lock:
            code, h = await self._request(CONNECT, header(TARGET, target), b"\x10\x00\x7f\xff")
            if code != OK:
                raise ObexError(f"CONNECT refused: 0x{code:02x}")
            self.connection_id = h.get(CONNECTION_ID, b"")

    async def setpath(self, name: str = "", up: bool = False) -> None:
        """name="" goes to root; up=True goes to the parent folder."""
        async with self.lock:
            flags = 0x02 | (0x01 if up else 0x00)
            headers = self._cid() + ("" if up else header(NAME, name))
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

    async def put(self, type_: str, name: str, body: bytes, params: bytes = b"") -> dict[int, bytes]:
        async with self.lock:
            headers = self._cid() + header(TYPE, type_.encode() + b"\x00") + header(NAME, name)
            if params:
                headers += header(APP_PARAMS, params)
            code, h = await self._request(PUT, headers + header(END_OF_BODY, body))
            if code != OK:
                raise ObexError(f"PUT {type_} failed: 0x{code:02x}")
            return h
