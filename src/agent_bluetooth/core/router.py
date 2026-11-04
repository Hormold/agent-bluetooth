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
import contextlib
import itertools
import logging
from typing import TYPE_CHECKING, Any

from .call_rules import rule_for, rules_need_caller
from .events import E
from .lines import LineBook
from .recording import save_tape, tape_for
from .tasks import Tasks

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from .context import Context
    from .events import Event
    from .lines import Line
    from .ports import AudioHandler, AudioKit, Bridge, Call, Directory, Phone

    EventHandler = Callable[[Line, dict[str, Any]], Awaitable[None]]

log = logging.getLogger(__name__)
ROOM_MODES = ("app", "agent")
DIAL_CLAIM_S = 30  # an outgoing call this soon after dial() is the one we placed
CALLER_ID_WAIT_S = 3.0  # how long a ring waits for caller id when rules need it
CALLER_ID_POLL_S = 0.2
EMPTY_ROOM_GRACE_S = 3.0  # reconnects show up as left+joined; don't hang up on those


class CallGoneError(RuntimeError):
    """The call ended while a transition for it was in flight."""


class RoomOpenError(RuntimeError):
    """The bridge could not open a room; nothing is kept, so it can be retried."""


class CallRouter:
    def __init__(  # noqa: PLR0913 - one argument per port the router drives
        self,
        ctx: Context,
        phone: Phone,
        bridge: Bridge,
        cfg: dict[str, Any],
        *,
        audio: AudioKit,
        directory: Directory | None = None,
    ) -> None:
        self.ctx = ctx
        self.bus = ctx.bus
        self.phone = phone
        self.room_bridge = bridge  # LiveKit, used for app/agent modes
        self.audio = audio  # WAV playback, stall phrases, recording
        self.directory = directory  # optional: contact lookup by number
        self.answer_mode: str = cfg.get("answer", "on_join")
        self.incoming_mode: str = cfg.get("incoming", cfg.get("default", "app"))
        self.outgoing_mode: str = cfg.get("outgoing", "app")
        self.agent_name: str = cfg.get("agent_name", "")
        self.play_path: str = cfg.get("play_file", "")
        self.stall_dir: str = cfg.get("stall_dir", "")  # folder of phrases for mode=stall
        self.stall_minutes: float = float(cfg.get("stall_minutes", 5))
        self.record_calls: bool = bool(cfg.get("record_calls", False))
        self.record_dir: str = cfg["record_dir"]
        self.rules: list[dict[str, Any]] = cfg.get("rules", [])
        self.hangup_on_empty: bool = bool(cfg.get("hangup_on_empty", True))
        self.rooms: dict[str, dict[str, Any]] = {}  # opened rooms only, never in-flight
        self._lines = LineBook()
        self.tasks = Tasks(f"{ctx.bus.instance}:router")  # per-call routing / empty-room checks
        self._closed = False  # daemon shutting down: no new handling starts
        self._tags = itertools.count(1)
        self._dial: dict[str, Any] | None = None  # what the next outgoing call is for
        self._on: dict[str, EventHandler] = {
            E.CALL_RINGING: self._on_call_start,
            E.CALL_DIALING: self._on_call_start,
            E.CALL_ACTIVE: self._on_call_start,
            E.CALL_AUDIO: self._on_call_audio,
            E.DTMF_RECEIVED: self._on_dtmf,
            E.PLAYBACK_DONE: self._on_playback_done,
            E.ROOM_JOINED: self._on_room_joined,
            E.ROOM_LEFT: self._on_room_left,
        }
        self.bus.subscribe(self.on_event)

    @property
    def calls(self) -> dict[str, Call]:
        return self._lines.calls()

    # event loop ------------------------------------------------------------
    async def on_event(self, event: Event) -> None:
        d = event.data
        if self._closed:
            return
        if event.type == E.CALL_ENDED:
            if line := self._lines.get(d["call"]["id"]):
                await self._end(line)
            return
        # Track the call on every event that carries it, handled or not.
        line = (
            self._lines.track(d["call"]) if "call" in d else self._lines.get(d.get("call_id", ""))
        )
        if line is not None and (handle := self._on.get(event.type)) is not None:
            await handle(line, d)

    async def _on_call_start(self, line: Line, _d: dict[str, Any]) -> None:
        """Ringing/dialing route the call; active routes it if nothing has yet
        (an ignored call answered by hand, an outgoing call we didn't place)."""
        if line.handler is None and (line.routing is None or line.routing.done()):
            line.routing = self.tasks.spawn(self._route(line), name=f"route:{line.call.id}")

    async def _on_call_audio(self, line: Line, d: dict[str, Any]) -> None:
        if not d.get("up"):
            return
        pipe = self.phone.audio()
        if pipe is None or line.ended:
            return  # (a tape opened after the call ended would never be saved)
        if self.record_calls:
            pipe.tap = tape_for(line, self.audio, self.record_dir, pipe.sample_rate)
        async with line.lock:  # after any transition in flight, to whoever owns the call then
            if line.handler is not None and not line.ended:
                await line.handler.attach_audio(line.call, pipe)

    async def _on_dtmf(self, _line: Line, d: dict[str, Any]) -> None:
        log.info(
            "DTMF %s from %s -> phone line",
            d.get("digit"),
            d.get("identity") or "room",
        )
        await self.phone.send_dtmf(str(d.get("digit", "")))

    async def _on_playback_done(self, line: Line, d: dict[str, Any]) -> None:
        async with line.lock:
            if line.ended or d.get("handler") != line.tag:
                log.info("ignoring playback.done on %s: not its current handler", line.call.id)
                return
            log.info("clip done on %s, hanging up", line.call.id)
            await self._hangup(line.call)

    async def _on_room_joined(self, line: Line, _d: dict[str, Any]) -> None:
        if line.call.state == "ringing" and self.answer_mode == "on_join":
            await self.phone.answer()

    async def _on_room_left(self, line: Line, d: dict[str, Any]) -> None:
        if not self.hangup_on_empty or d.get("others", 0) != 0:
            return
        if line.call.state in ("active", "dialing"):
            if line.empty_check is not None:
                line.empty_check.cancel()
            line.empty_check = self.tasks.spawn(
                self._hangup_if_still_empty(line), name=f"empty-room:{line.call.id}"
            )

    async def _hangup_if_still_empty(self, line: Line, grace: float = EMPTY_ROOM_GRACE_S) -> None:
        """A participant reconnecting (or a duplicate identity being replaced)
        shows up as left+joined in the same instant; only hang up if the room
        is really still empty after a short grace period."""
        await asyncio.sleep(grace)
        async with line.lock:
            if line.ended or line.handler is not self.room_bridge:
                return  # call over, or moved out of the room (play, stall)
            count = getattr(self.room_bridge, "participants", None)
            if count is not None and count(line.call) > 0:
                return
            log.info("room for %s empty, hanging up", line.call.id)
            await self._hangup(line.call)

    async def _hangup(self, call: Call) -> None:
        """Hang up `call`, and only it: phone.hangup() acts on whatever call the
        phone has now, which may already be a different one."""
        live = (self.phone.status() or {}).get("call") or {}
        if live.get("id") != call.id:
            log.info("not hanging up %s: the phone is on %s", call.id, live.get("id"))
            return
        await self.phone.hangup()

    # policy ----------------------------------------------------------------
    async def _route(self, line: Line) -> None:
        """Automatic routing, one task per call; cancelled when the call ends."""
        call = line.call
        if call.direction == "in" and not call.number and rules_need_caller(self.rules):
            await self._wait_for_number(call)
        self._identify(call)
        async with line.lock:
            if line.ended or line.handler is not None:
                return  # ended while we waited, or the API took the call meanwhile
            try:
                await self._apply(line)
            except CallGoneError:
                log.info("call %s ended while being routed", call.id)
            except RoomOpenError as exc:
                log.warning("%s; the call is left unrouted (bridge it to retry)", exc)

    async def _apply(self, line: Line) -> None:
        call = line.call
        if call.direction == "out" and (dial := self._take_dial()):
            await self._place(line, dial)
            return
        rule = rule_for(self.rules, call)
        mode = rule.get("mode", rule.get("action", "app")) if rule else self._default_mode(call)
        log.info(
            "call %s (%s) from %s -> mode=%s",
            call.id,
            call.direction,
            call.number or "?",
            mode,
        )
        await self._start_mode(line, mode, rule or {})

    async def _start_mode(self, line: Line, mode: str, rule: dict[str, Any]) -> None:
        """Run `mode` for the call; a rule's own agent/file/folder beat the defaults.
        ignore (and reject on an outgoing call) leaves the call alone."""
        if mode == "reject" and line.call.direction == "in":
            await self._hangup(line.call)
        elif mode in ROOM_MODES:
            agent_name = (rule.get("agent_name") or self.agent_name) if mode == "agent" else ""
            await self._to_room(line, agent_name)
        elif mode == "play":
            await self._start_play(line, rule.get("play_file") or self.play_path)
        elif mode == "stall":
            stall_dir = rule.get("stall_dir") or self.stall_dir
            tag = self._tag(line)
            await self._use(line, self.audio.stall(stall_dir, self.stall_minutes, tag=tag), tag)

    async def _start_play(self, line: Line, play_path: str) -> None:
        if play_path:
            await self._play(line, play_path)
        else:
            log.warning("play mode but no play_file set; falling back to app")
            await self._to_room(line)

    # outgoing calls placed by an agent -------------------------------------
    async def dial(self, number: str, room: str = "", agent_name: str = "", task: str = "") -> None:
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
        try:
            await self.phone.dial(number)
        except Exception:
            self._dial = None  # a dial that never happened must not claim the next call
            raise

    def _take_dial(self) -> dict[str, Any] | None:
        dial, self._dial = self._dial, None
        if dial and asyncio.get_running_loop().time() - dial["at"] < DIAL_CLAIM_S:
            return dial
        return None

    async def _place(self, line: Line, dial: dict[str, Any]) -> None:
        call = line.call
        call.number = call.number or dial["number"]
        call.room, call.task = dial["room"], dial["task"]
        agent_name = dial["agent_name"]
        log.info(
            "call %s to %s -> %s",
            call.id,
            call.number,
            f"room {call.room}" if call.room else f"agent {agent_name or '(none)'}",
        )
        await self._to_room(line, agent_name)

    def _default_mode(self, call: Call) -> str:
        return self.incoming_mode if call.direction == "in" else self.outgoing_mode

    async def _wait_for_number(self, call: Call) -> None:
        """Caller id often lands a moment after the ring; rules need it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CALLER_ID_WAIT_S
        while loop.time() < deadline and not call.number:
            await asyncio.sleep(CALLER_ID_POLL_S)
            live = (self.phone.status() or {}).get("call") or {}
            if live.get("id") == call.id and live.get("number"):
                call.number = live["number"]
                call.name = call.name or live.get("name", "")

    def _identify(self, call: Call) -> None:
        """Mark known contacts and fill the name from the phonebook."""
        if self.directory is None or not call.number:
            return
        name = self.directory.lookup(call.number)
        call.contact = name is not None
        if name and not call.name:
            call.name = name

    # handlers / runtime overrides (API + agent) ---------------------------
    async def route_to_room(
        self, call: Call, *, agent: bool = False, agent_name: str = ""
    ) -> dict[str, Any]:
        async with self._transition(call) as line:
            return await self._to_room(line, (agent_name or self.agent_name) if agent else "")

    async def play_file(
        self,
        call: Call,
        path: str,
        *,
        loop: bool = False,
        hangup_after: bool = True,
    ) -> None:
        async with self._transition(call) as line:
            await self._play(line, path, loop=loop, hangup_after=hangup_after)

    def join_info(self, room: str, identity: str) -> dict[str, Any]:
        return self.room_bridge.join_info(room, identity)

    @contextlib.asynccontextmanager
    async def _transition(self, call: Call) -> AsyncGenerator[Line]:
        """Hold the call's lock for an API-driven change; the call must be live."""
        line = self._lines.get(call.id)
        if line is None:
            raise CallGoneError(f"call {call.id} is not active")
        async with line.lock:
            if line.ended:
                raise CallGoneError(f"call {call.id} ended")
            yield line

    # transitions (caller holds line.lock) ---------------------------------
    async def _to_room(self, line: Line, agent_name: str = "") -> dict[str, Any]:
        info = self.rooms.get(line.call.id)
        if info is None:
            info = await self._open_room(line, agent_name)
        await self._use(line, self.room_bridge, "", answer_on_join=True)
        return info

    async def _play(
        self, line: Line, path: str, *, loop: bool = False, hangup_after: bool = True
    ) -> None:
        tag = self._tag(line)
        player = self.audio.player(path, tag=tag, loop=loop, hangup_after=hangup_after)
        await self._use(line, player, tag)

    async def _use(
        self, line: Line, handler: AudioHandler, tag: str, *, answer_on_join: bool = False
    ) -> None:
        """Make `handler` the call's one audio handler. The old one is stopped
        first, so two never write into the same line."""
        old = line.handler
        if old is not None and old is not handler:
            line.handler = None
            # Shielded: if the call ends meanwhile (routing cancelled), the old
            # handler still stops completely instead of half-closed and unowned.
            await asyncio.shield(self._release(line, old))
        line.handler, line.tag = handler, tag
        call = line.call
        if call.state == "ringing":
            # room modes with on_join wait for a joiner; everything else answers now
            if not (answer_on_join and self.answer_mode == "on_join"):
                await self.phone.answer()
        elif (pipe := self.phone.audio()) is not None:
            await handler.attach_audio(call, pipe)

    async def _release(self, line: Line, handler: AudioHandler) -> None:
        """Stop one handler; for the room bridge that means closing the room."""
        if handler is self.room_bridge:
            await self._close_room(line)
        else:
            await handler.close(line.call)

    async def _open_room(self, line: Line, agent_name: str) -> dict[str, Any]:
        """Only a room that opened is kept: a failed open leaves nothing behind,
        so the next attempt (or automatic routing) tries again."""
        call = line.call
        try:
            info = await self.room_bridge.open(call, agent_name=agent_name)
        except (OSError, RuntimeError) as exc:  # the Bridge port's "can't right now"
            raise RoomOpenError(f"could not open a room for call {call.id}: {exc}") from exc
        if line.ended:  # ended mid-open; _end is waiting for our lock and finds no room
            await self.room_bridge.close(call)
            raise CallGoneError(f"call {call.id} ended while its room opened")
        self.rooms[call.id] = info
        self.bus.publish(
            E.ROOM_OPENED,
            call_id=call.id,
            number=call.number,
            name=call.name,
            direction=call.direction,
            agent_name=agent_name,
            **info,
        )
        return info

    async def _close_room(self, line: Line) -> None:
        if self.rooms.pop(line.call.id, None) is None:
            return
        await self.room_bridge.close(line.call)
        self.bus.publish(E.ROOM_CLOSED, call_id=line.call.id)

    async def _end(self, line: Line) -> None:
        """The call is over: stop routing, then undo whatever a transition set up
        (waiting for one in flight, so nothing it opens outlives the call)."""
        self._lines.retire(line)
        for task in (line.routing, line.empty_check):
            if task is not None:
                task.cancel()
        save_tape(line, self.bus)
        try:
            async with line.lock:
                if line.handler is not None:
                    handler, line.handler = line.handler, None
                    await self._release(line, handler)
                await self._close_room(line)
        finally:
            self._lines.released(line)

    async def aclose(self) -> None:
        """Daemon shutdown: end every call's handling (players, tapes, rooms),
        wait for ends already in flight, then stop the router's own tasks. The
        phone is left alone: stopping the daemon doesn't hang anyone up."""
        self._closed = True
        for line in self._lines:
            try:
                await self._end(line)
            except Exception:  # one call's handler failing to stop must not keep the rest
                log.warning("could not release call %s", line.call.id, exc_info=True)
        for line in list(self._lines.ending):
            await line.released.wait()
        await self.tasks.aclose()

    # helpers ---------------------------------------------------------------
    def _tag(self, line: Line) -> str:
        return f"{line.call.id}:{next(self._tags)}"
