"""Call routing: phone events in, a chosen MODE applied — same options for
incoming and outgoing calls.

MODES (set per direction via settings, overridable per call over the API):
  app     audio into a LiveKit room; a human joins from the app to talk
  agent   audio into a LiveKit room + dispatch a LiveKit agent (metadata carries
          the call's phone number)
  play    play a WAV file into the line
  record  record the call audio to a WAV file
  reject  (incoming only) hang up without answering
  ignore  do nothing; decide later over the API

For room modes (app/agent) the phone is answered when someone joins the room
(answer="on_join") or immediately (answer="auto"). For play/record the call is
answered at once so audio flows. Outgoing calls attach the same handler once the
audio link is up.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from typing import Any

from ..adapters.audio.file_player import FilePlayer
from ..adapters.audio.stall import StallPlayer
from ..adapters.bridge.recorder import RecorderBridge
from .context import Context
from .events import E, Event
from .ports import AudioHandler, Bridge, Call, Phone

log = logging.getLogger(__name__)
ROOM_MODES = ("app", "agent")


class CallRouter:
    def __init__(
        self,
        ctx: Context,
        phone: Phone,
        bridge: Bridge,
        cfg: dict[str, Any],
        directory: Any = None,
    ):
        self.ctx = ctx
        self.bus = ctx.bus
        self.phone = phone
        self.room_bridge = bridge  # LiveKit, used for app/agent modes
        self.directory = directory  # optional: contact lookup by number
        self.answer_mode: str = cfg.get("answer", "on_join")
        self.incoming_mode: str = cfg.get("incoming", cfg.get("default", "app"))
        self.outgoing_mode: str = cfg.get("outgoing", "app")
        self.agent_name: str = cfg.get("agent_name", "")
        self.play_path: str = cfg.get("play_file", "")
        self.stall_dir: str = cfg.get(
            "stall_dir", ""
        )  # folder of phrases for mode=stall
        self.stall_minutes: float = float(cfg.get("stall_minutes", 5))
        self.record_dir: str = cfg.get("record_dir", "./recordings")
        self.rules: list[dict[str, Any]] = cfg.get("rules", [])
        self.hangup_on_empty: bool = bool(cfg.get("hangup_on_empty", True))
        self.calls: dict[str, Call] = {}
        self.handlers: dict[str, AudioHandler] = {}
        self.rooms: dict[str, dict[str, Any]] = {}
        self._recorder: RecorderBridge | None = None
        self._dial: dict[str, Any] | None = None  # what the next outgoing call is for
        self.bus.subscribe(self.on_event)

    # event loop ------------------------------------------------------------
    async def on_event(self, event: Event) -> None:
        d = event.data
        call = (
            self._call(d["call"])
            if "call" in d
            else self.calls.get(d.get("call_id", ""))
        )
        if call is None:
            return
        match event.type:
            case E.CALL_RINGING | E.CALL_DIALING:
                await self._apply(call)
            case E.CALL_ACTIVE:
                if call.id not in self.handlers:
                    await self._apply(call)
            case E.CALL_AUDIO if d.get("up"):
                handler = self.handlers.get(call.id)
                if handler is not None and (pipe := self.phone.audio()) is not None:
                    await handler.attach_audio(call, pipe)
            case E.CALL_ENDED:
                await self._cleanup(call)
            case E.DTMF_RECEIVED:
                log.info(
                    "DTMF %s from %s -> phone line",
                    d.get("digit"),
                    d.get("identity") or "room",
                )
                await self.phone.send_dtmf(str(d.get("digit", "")))
            case E.PLAYBACK_DONE:
                log.info("clip done on %s, hanging up", call.id)
                await self.phone.hangup()
            case E.ROOM_JOINED if (
                call.state == "ringing" and self.answer_mode == "on_join"
            ):
                await self.phone.answer()
            case E.ROOM_LEFT if self.hangup_on_empty and d.get("others", 0) == 0:
                if call.state in ("active", "dialing"):
                    asyncio.create_task(self._hangup_if_still_empty(call))

    async def _hangup_if_still_empty(self, call: Call, grace: float = 3.0) -> None:
        """A participant reconnecting (or a duplicate identity being replaced)
        shows up as left+joined in the same instant; only hang up if the room
        is really still empty after a short grace period."""
        await asyncio.sleep(grace)
        handler = self.handlers.get(call.id)
        count = getattr(handler, "participants", None)
        if call.id not in self.calls or (count is not None and count(call) > 0):
            return
        log.info("room for %s empty, hanging up", call.id)
        await self.phone.hangup()

    # policy ----------------------------------------------------------------
    async def _apply(self, call: Call) -> None:
        if call.id in self.handlers:
            return
        if call.direction == "in" and not call.number and self._rules_need_caller():
            await self._wait_for_number(call)
        self._identify(call)
        if call.direction == "out" and (dial := self._take_dial()):
            await self._place(call, dial)
            return
        rule = self._rule_for(call)
        mode = (
            rule.get("mode", rule.get("action", "app"))
            if rule
            else self._default_mode(call)
        )
        agent_name = (rule or {}).get("agent_name") or self.agent_name
        play_path = (rule or {}).get("play_file") or self.play_path
        stall_dir = (rule or {}).get("stall_dir") or self.stall_dir
        log.info(
            "call %s (%s) from %s -> mode=%s",
            call.id,
            call.direction,
            call.number or "?",
            mode,
        )
        if mode == "reject" and call.direction == "in":
            await self.phone.hangup()
            return
        if mode == "ignore":
            return
        if mode in ROOM_MODES:
            await self.route_to_room(
                call, agent=(mode == "agent"), agent_name=agent_name
            )
        elif mode == "play":
            if play_path:
                await self.play_file(call, play_path)
            else:
                log.warning("play mode but no play_file set; falling back to app")
                await self.route_to_room(call)
        elif mode == "record":
            await self.record(call)
        elif mode == "stall":
            handler = self.handlers[call.id] = StallPlayer(
                self.bus, stall_dir, self.stall_minutes
            )
            await self._answer_or_attach(call, handler)

    # outgoing calls placed by an agent -------------------------------------
    async def dial(
        self, number: str, room: str = "", agent_name: str = "", task: str = ""
    ) -> None:
        """Place a call and send it to a room. `room`: join the phone into an
        existing room (an agent that dials, like CreateSIPParticipant).
        `agent_name`: open a new room and dispatch that agent, with `task` in
        its metadata."""
        self._dial = {
            "number": number,
            "room": room,
            "agent_name": agent_name,
            "task": task,
            "at": asyncio.get_running_loop().time(),
        }
        await self.phone.dial(number)

    def _take_dial(self) -> dict[str, Any] | None:
        dial, self._dial = self._dial, None
        if dial and asyncio.get_running_loop().time() - dial["at"] < 30:
            return dial
        return None

    async def _place(self, call: Call, dial: dict[str, Any]) -> None:
        call.number = call.number or dial["number"]
        call.room, call.task = dial["room"], dial["task"]
        agent_name = dial["agent_name"]
        log.info(
            "call %s to %s -> %s",
            call.id,
            call.number,
            f"room {call.room}" if call.room else f"agent {agent_name or '(none)'}",
        )
        await self.route_to_room(call, agent=bool(agent_name), agent_name=agent_name)

    def _rule_for(self, call: Call) -> dict[str, Any] | None:
        """First matching rule. Conditions (all optional): direction in|out|any,
        when_number (glob on the number), when_contact (true/false)."""
        for rule in self.rules:
            direction = rule.get("direction", "in")
            if direction != "any" and call.direction != direction:
                continue
            pattern = rule.get("when_number")
            if pattern and not _number_matches(call.number or "", pattern):
                continue
            if "when_contact" in rule and bool(rule["when_contact"]) != call.contact:
                continue
            return rule
        return None

    def _default_mode(self, call: Call) -> str:
        return self.incoming_mode if call.direction == "in" else self.outgoing_mode

    def _rules_need_caller(self) -> bool:
        return any("when_number" in r or "when_contact" in r for r in self.rules)

    async def _wait_for_number(self, call: Call, timeout: float = 3.0) -> None:
        """Caller id often lands a moment after the ring; rules need it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline and not call.number:
            await asyncio.sleep(0.2)
            live = (self.phone.status() or {}).get("call") or {}
            if live.get("id") == call.id and live.get("number"):
                call.number = live["number"]
                call.name = call.name or live.get("name", "")

    def _identify(self, call: Call) -> None:
        """Mark known contacts and fill the name from the phonebook."""
        lookup = getattr(self.directory, "lookup", None)
        if not lookup or not call.number:
            return
        name = lookup(call.number)
        call.contact = name is not None
        if name and not call.name:
            call.name = name

    # handlers / runtime overrides (API + agent) ---------------------------
    async def route_to_room(
        self, call: Call, agent: bool = False, agent_name: str = ""
    ) -> dict[str, Any]:
        self.room_bridge.agent_name = (agent_name or self.agent_name) if agent else ""
        self.handlers[call.id] = self.room_bridge
        info = await self._open_room(call)
        await self._answer_or_attach(call, self.room_bridge, answer_on_join=True)
        return info

    async def play_file(
        self, call: Call, path: str, loop: bool = False, hangup_after: bool = True
    ) -> None:
        handler = self.handlers[call.id] = FilePlayer(
            self.bus, path, loop, hangup_after
        )
        await self._answer_or_attach(call, handler)

    async def record(self, call: Call) -> None:
        if self._recorder is None:
            self._recorder = RecorderBridge(self.ctx, {"dir": self.record_dir})
        self.handlers[call.id] = self._recorder
        await self._answer_or_attach(call, self._recorder)

    async def _answer_or_attach(
        self, call: Call, handler: AudioHandler, answer_on_join: bool = False
    ) -> None:
        if call.state == "ringing":
            # room modes with on_join wait for a joiner; everything else answers now
            if not (answer_on_join and self.answer_mode == "on_join"):
                await self.phone.answer()
        elif (pipe := self.phone.audio()) is not None:
            await handler.attach_audio(call, pipe)

    # helpers ---------------------------------------------------------------
    def _call(self, data: dict[str, Any]) -> Call:
        call = self.calls.get(data["id"])
        if call is None:
            call = self.calls[data["id"]] = Call(**data)
        else:
            for k, v in data.items():
                if k in _ROUTER_KNOWS and not v:
                    continue  # the phone doesn't know it; keep what we found
                setattr(call, k, v)
        return call

    async def _open_room(self, call: Call) -> dict[str, Any]:
        if call.id in self.rooms:
            return self.rooms[call.id]
        self.rooms[call.id] = {}
        info = self.rooms[call.id] = await self.room_bridge.open(call)
        self.bus.publish(
            E.ROOM_OPENED,
            call_id=call.id,
            number=call.number,
            name=call.name,
            direction=call.direction,
            **info,
        )
        return info

    def join_info(self, room: str, identity: str) -> dict[str, Any]:
        return self.room_bridge.join_info(room, identity)

    async def _cleanup(self, call: Call) -> None:
        self.calls.pop(call.id, None)
        handler = self.handlers.pop(call.id, None)
        if handler is not None:
            await handler.close(call)
        if self.rooms.pop(call.id, None) is not None:
            self.bus.publish(E.ROOM_CLOSED, call_id=call.id)


# Filled in by the router (contacts, agent dials); phone events send them empty.
_ROUTER_KNOWS = ("number", "name", "contact", "room", "task")


def _number_matches(number: str, pattern: str) -> bool:
    """Glob patterns (+1800*) match the raw number; a plain number matches on
    digits, so "+1 (415) 555-0142" and "4155550142" are the same caller."""
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(number, pattern)
    a = "".join(c for c in number if c.isdigit())[-10:]
    b = "".join(c for c in pattern if c.isdigit())[-10:]
    return bool(a) and a == b
