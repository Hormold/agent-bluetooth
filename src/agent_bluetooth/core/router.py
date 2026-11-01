"""Call routing: phone events in, actions out. All policy lives here.

Each incoming call gets an ACTION, picked by the first matching rule (else the
default):
  room    answer and bridge audio into a LiveKit room (humans / agents join)
  play    answer and play a WAV file into the line, then hang up
  reject  hang up without answering
  ignore  do nothing; an agent decides at runtime over the API

ANSWER MODE applies to the `room` action only:
  auto     pick up immediately
  on_join  keep ringing until someone joins the room (rings out otherwise)
  manual   never pick up by itself

An agent can override any call at runtime: route_to_room() / play_file() /
answer() / hangup() — e.g. after inspecting the number from a `call.ringing`
event on the `watch` stream.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from ..adapters.audio.file_player import FilePlayer
from .context import Context
from .events import E, Event
from .ports import AudioHandler, Bridge, Call, Phone

log = logging.getLogger(__name__)
ACTIONS = ("room", "play", "reject", "ignore")


class CallRouter:
    def __init__(self, ctx: Context, phone: Phone, bridge: Bridge, cfg: dict[str, Any]):
        self.ctx = ctx
        self.bus = ctx.bus
        self.phone = phone
        self.bridge = bridge
        self.answer_mode: str = cfg.get("answer", "on_join")
        self.default_action: str = cfg.get("default", "room")
        self.rules: list[dict[str, Any]] = cfg.get("rules", [])
        self.hangup_on_empty: bool = bool(cfg.get("hangup_on_empty", True))
        self.calls: dict[str, Call] = {}
        self.handlers: dict[str, AudioHandler] = {}  # audio target per call
        self.rooms: dict[str, dict[str, Any]] = {}
        self.bus.subscribe(self.on_event)

    # event loop ------------------------------------------------------------
    async def on_event(self, event: Event) -> None:
        d = event.data
        call = self._call(d["call"]) if "call" in d else self.calls.get(d.get("call_id", ""))
        if call is None:
            return
        match event.type:
            case E.CALL_RINGING:
                await self._decide(call)
            case E.CALL_DIALING:
                self.handlers.setdefault(call.id, self.bridge)
                await self._open_room(call)
            case E.CALL_ACTIVE:
                if call.id not in self.handlers:  # answered outside our policy
                    self.handlers[call.id] = self.bridge
                    await self._open_room(call)
            case E.CALL_AUDIO if d.get("up"):
                if (pipe := self.phone.audio()) is not None:
                    await self.handlers.get(call.id, self.bridge).attach_audio(call, pipe)
            case E.CALL_ENDED:
                await self._cleanup(call)
            case E.PLAYBACK_DONE:
                log.info("clip done on %s, hanging up", call.id)
                await self.phone.hangup()
            case E.ROOM_JOINED if call.state == "ringing" and self.answer_mode == "on_join":
                await self.phone.answer()
            case E.ROOM_LEFT if self.hangup_on_empty and d.get("others", 0) == 0:
                if call.state in ("active", "dialing"):
                    log.info("room for %s empty, hanging up", call.id)
                    await self.phone.hangup()

    # policy ----------------------------------------------------------------
    async def _decide(self, call: Call) -> None:
        action, params = self._match(call)
        log.info("call %s from %s -> action=%s", call.id, call.number or "?", action)
        match action:
            case "reject":
                await self.phone.hangup()
            case "ignore":
                pass
            case "play":
                await self.play_file(call, params["file"], params.get("loop", False), params.get("hangup_after", True))
            case _:  # room
                await self.route_to_room(call)

    def _match(self, call: Call) -> tuple[str, dict[str, Any]]:
        for rule in self.rules:
            if call.direction != rule.get("direction", "in"):
                continue
            pattern = rule.get("when_number")
            if pattern and not fnmatch.fnmatch(call.number or "", pattern):
                continue
            action = rule.get("action", "room")
            return (action if action in ACTIONS else "room"), rule
        return self.default_action, {}

    # runtime overrides (API / agent) --------------------------------------
    async def route_to_room(self, call: Call) -> dict[str, Any]:
        self.handlers[call.id] = self.bridge
        info = await self._open_room(call)
        if self.answer_mode == "auto" and call.state == "ringing":
            await self.phone.answer()
        elif call.state in ("active", "dialing") and (pipe := self.phone.audio()):
            await self.bridge.attach_audio(call, pipe)
        return info

    async def play_file(self, call: Call, path: str, loop: bool = False, hangup_after: bool = True) -> None:
        self.handlers[call.id] = FilePlayer(self.bus, path, loop, hangup_after)
        if call.state == "ringing":
            await self.phone.answer()
        elif (pipe := self.phone.audio()) is not None:
            await self.handlers[call.id].attach_audio(call, pipe)

    # helpers ---------------------------------------------------------------
    def _call(self, data: dict[str, Any]) -> Call:
        call = self.calls.get(data["id"])
        if call is None:
            call = self.calls[data["id"]] = Call(**data)
        else:
            for k, v in data.items():
                setattr(call, k, v)
        return call

    async def _open_room(self, call: Call) -> dict[str, Any]:
        if call.id in self.rooms:
            return self.rooms[call.id]
        self.rooms[call.id] = {}  # reserve before awaiting: events race
        info = self.rooms[call.id] = await self.bridge.open(call)
        self.bus.publish(
            E.ROOM_OPENED, call_id=call.id, number=call.number, name=call.name,
            direction=call.direction, **info,
        )
        return info

    async def _cleanup(self, call: Call) -> None:
        self.calls.pop(call.id, None)
        handler = self.handlers.pop(call.id, None)
        if handler is not None:
            await handler.close(call)
        if self.rooms.pop(call.id, None) is not None:
            self.bus.publish(E.ROOM_CLOSED, call_id=call.id)
