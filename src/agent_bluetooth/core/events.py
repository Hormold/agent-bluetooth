"""Events are the only thing components share. Every adapter speaks them."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


class E:
    """Event type names. Plain strings so webhooks/agents can match on them."""

    PHONE_CONNECTED = "phone.connected"
    PHONE_DISCONNECTED = "phone.disconnected"
    PHONE_STATUS = "phone.status"  # signal, battery, volume, ...

    CALL_RINGING = "call.ringing"  # incoming, not answered yet
    CALL_DIALING = "call.dialing"  # outgoing, not connected yet
    CALL_ACTIVE = "call.active"
    CALL_ENDED = "call.ended"
    CALL_AUDIO = "call.audio"  # audio link up/down: {call, up, sample_rate}

    ROOM_OPENED = "room.opened"  # call audio is live in a room
    ROOM_JOINED = "room.joined"  # someone (human/agent) joined the call room
    ROOM_LEFT = "room.left"
    ROOM_CLOSED = "room.closed"

    PLAYBACK_DONE = "playback.done"  # a FilePlayer clip finished: {call_id, file}
    RECORDING_SAVED = (
        "recording.saved"  # a call recording was written: {call_id, file, seconds}
    )

    SMS_RECEIVED = "sms.received"
    SMS_SENT = "sms.sent"

    DIRECTORY_SYNCED = (
        "directory.synced"  # contacts/history pulled from the phone: {kind, count}
    )


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
