"""PBAP (Phone Book Access Profile) client over our OBEX layer: pull the phone's
contacts and call history, the way a car head unit shows them.

Read-only. Works on Android and iPhone (both expose a PBAP server). vCards are
parsed minimally — name + numbers + call-log timestamp — which is all the dialer
needs; we deliberately don't try to model every vCard field."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bumble import rfcomm
from bumble.core import UUID

from .obex import ObexClient, app_params

if TYPE_CHECKING:
    from bumble.device import Connection

    from .radio import BumbleRadio

log = logging.getLogger(__name__)

PBAP_UUID = UUID.from_16_bits(0x112F)  # Phonebook Access - PSE
PBAP_TARGET = bytes.fromhex("796135f0f0c511d809660800200c9a66")
TYPE_PHONEBOOK = "x-bt/phonebook"
# app-parameter tags
T_MAX_LIST, T_FILTER, T_FORMAT = 0x04, 0x06, 0x07

# Standard phonebook objects. Combined call history = cch; others split by kind.
PB_CONTACTS = "telecom/pb.vcf"
PB_HISTORY = "telecom/cch.vcf"


@dataclass
class Contact:
    name: str
    numbers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, str | list[str]]:
        return {"name": self.name, "numbers": self.numbers}


@dataclass
class HistoryEntry:
    name: str
    number: str
    kind: str  # received | dialed | missed | ""
    at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "number": self.number,
            "kind": self.kind,
            "at": self.at,
        }


# PBAP vCard property mask (PBAP spec 5.1.4.1)
_VERSION, _FN, _N, _TEL, _CALL_DATETIME = 1 << 0, 1 << 1, 1 << 2, 1 << 7, 1 << 28
CONTACT_FIELDS = _VERSION | _FN | _N | _TEL
HISTORY_FIELDS = CONTACT_FIELDS | _CALL_DATETIME


class BumblePbap:
    def __init__(self, radio: BumbleRadio) -> None:
        self.radio = radio

    async def _pull(self, address: str, obj: str, max_count: int, fields: int) -> str:
        conn = self._connection(address)
        if conn is None:
            raise RuntimeError("phone not connected")
        channel = await rfcomm.find_rfcomm_channel_with_uuid(conn, PBAP_UUID)
        if channel is None:
            raise RuntimeError("phone has no PBAP service")
        mux = await self.radio.multiplexer(conn)
        obex = ObexClient(await mux.open_dlc(channel))
        try:
            await obex.connect(PBAP_TARGET)
            params = app_params(
                m=(T_MAX_LIST, 2, max_count),
                fmt=(T_FORMAT, 1, 1),  # vCard 3.0
                # Only the fields we use. 0 means "everything", which includes
                # contact PHOTOs and turns a phonebook pull into minutes.
                flt=(T_FILTER, 8, fields),
            )
            return (await obex.get(TYPE_PHONEBOOK, obj, params)).decode("utf-8", "replace")
        finally:
            await obex.aclose()

    def _connection(self, address: str) -> Connection | None:
        for c in self.radio.device.connections.values():
            if str(c.peer_address) == address:
                return c
        return None

    async def contacts(self, address: str, limit: int = 1000) -> list[Contact]:
        vcf = await self._pull(address, PB_CONTACTS, limit, CONTACT_FIELDS)
        out = [c for c in (_parse_contact(v) for v in _split_vcards(vcf)) if c and c.numbers]
        log.info("PBAP: %d contacts", len(out))
        return out

    async def history(self, address: str, limit: int = 100) -> list[HistoryEntry]:
        vcf = await self._pull(address, PB_HISTORY, limit, HISTORY_FIELDS)
        out = [h for h in (_parse_history(v) for v in _split_vcards(vcf)) if h]
        log.info("PBAP: %d call-history entries", len(out))
        return out


def _split_vcards(text: str) -> list[str]:
    cards: list[str] = []
    cur: list[str] = []
    for line in text.splitlines():
        if line.startswith("BEGIN:VCARD"):
            cur = []
        elif line.startswith("END:VCARD"):
            cards.append("\n".join(cur))
        else:
            cur.append(line)
    return cards


def _unfold(card: str) -> list[str]:
    # vCard line folding: a leading space/tab continues the previous line.
    lines: list[str] = []
    for line in card.splitlines():
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _parse_contact(card: str) -> Contact | None:
    name, numbers = "", []
    for line in _unfold(card):
        key, _, value = line.partition(":")
        key = key.split(";")[0].upper()
        if key == "FN":
            name = value.strip()
        elif key == "TEL":
            num = value.strip()
            if num and num not in numbers:
                numbers.append(num)
    if not name and numbers:
        name = numbers[0]
    return Contact(name=name, numbers=numbers) if (name or numbers) else None


def _parse_history(card: str) -> HistoryEntry | None:
    name, number, at, kind = "", "", "", ""
    kinds = {"RECEIVED": "received", "DIALED": "dialed", "MISSED": "missed"}
    for line in _unfold(card):
        key, _, value = line.partition(":")
        upper = key.upper()
        base = upper.split(";")[0]
        if base == "FN":
            name = value.strip()
        elif base == "TEL":
            number = value.strip()
        elif base == "X-IRMC-CALL-DATETIME":
            at = value.strip()
            for tag, k in kinds.items():
                if tag in upper:
                    kind = k
    if not number:
        return None
    return HistoryEntry(name=name or number, number=number, kind=kind, at=at)
