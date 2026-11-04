"""HTTP + SSE API. Small on purpose: agents can drive the phone with curl."""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from aiohttp import web

from ..core.actions import as_bool
from ..core.errors import PhoneCommandError
from ..core.events import E
from ..core.ports import Simulator
from ..core.settings import apply as settings_apply
from ..core.settings import current as settings_current
from ..core.settings import validate as settings_validate
from ..core.tasks import Tasks

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiohttp.typedefs import Handler

    from ..app import App
    from ..core.events import Event
    from ..core.ports import Call, Directory, Phone

    PhoneCommand = Callable[[Phone, dict[str, Any]], Awaitable[None]]

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"
SMS_KEPT = 1000  # newest SMS events kept for GET /api/sms; also its max limit
SMS_DEFAULT_LIMIT = "20"
STATUS_RECENT_EVENTS = 50
PAIR_DEFAULT_S = 120


@runtime_checkable
class _Simulating(Simulator, Protocol):
    simulates: frozenset[str]  # the `kind`s this adapter can fake


class HttpApi:
    def __init__(self, app: App, cfg: dict[str, Any]) -> None:
        self.app = app
        self.host = cfg.get("host", "127.0.0.1")  # exposing the phone is opt-in
        self.port = int(cfg.get("port", 8080))
        self.api_key = cfg.get("api_key", "")
        self._runner: web.AppRunner | None = None
        self._tasks = Tasks(f"{app.name}:http")  # dials that outlive their request
        self.started_at = time.time()
        self.sms: deque[Event] = deque(maxlen=SMS_KEPT)
        self.app.ctx.bus.subscribe(self._capture_sms)
        self.logs = _RingLog()
        logging.getLogger().addHandler(self.logs)

    async def start(self) -> None:
        server = web.Application(middlewares=[self._auth])
        r = server.router
        r.add_get("/", self.index)
        r.add_get("/api/status", self.status)
        r.add_get("/api/events", self.events)
        r.add_post("/api/calls", self.dial)
        r.add_post("/api/calls/answer", self._cmd(lambda p, _: p.answer()))
        r.add_post("/api/calls/hangup", self._cmd(lambda p, _: p.hangup()))
        r.add_post("/api/calls/dtmf", self._cmd(lambda p, b: p.send_dtmf(b["digits"])))
        r.add_post("/api/calls/play", self.play)
        r.add_post("/api/calls/bridge", self.bridge)
        r.add_post("/api/volume", self._cmd(lambda p, b: p.set_volume(int(b["level"]))))
        r.add_post("/api/sms", self.send_sms)
        r.add_get("/api/sms", self.list_sms)
        r.add_get("/api/contacts", self.contacts)
        r.add_get("/api/calls/history", self.history)
        r.add_get("/api/directory", self.directory_info)
        r.add_post("/api/directory/sync", self.directory_sync)
        r.add_get("/api/debug", self.debug)
        r.add_get("/api/phones", self.phones)
        r.add_post("/api/pair", self.pair)
        r.add_post("/api/phones/forget", self.forget_phone)
        r.add_get("/api/settings", self.get_settings)
        r.add_post("/api/settings", self.set_settings)
        r.add_post("/api/token", self.token)
        r.add_post("/api/simulate/{kind}", self.simulate)
        self._runner = web.AppRunner(server, handler_cancellation=True)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()
        log.info("http api on http://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        await self._tasks.aclose()

    @web.middleware
    async def _auth(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        if self.api_key and request.path.startswith("/api/"):
            given = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if (given or request.query.get("key")) != self.api_key:
                return web.json_response({"error": "bad api key"}, status=401)
        try:
            return await handler(request)
        except (ValueError, KeyError) as e:
            return web.json_response({"error": str(e)}, status=400)
        except web.HTTPException as e:
            return web.json_response({"error": e.text}, status=e.status)
        except PhoneCommandError as e:
            # The phone was there and got the command but didn't do it: an
            # upstream failure (502), or no reply in time (504).
            status = 504 if e.reason == "timeout" else 502
            return web.json_response(e.as_dict(), status=status)
        except RuntimeError as e:
            # Adapters raise RuntimeError for "not right now" (phone not connected):
            # that's a 503 the client can show, not a server crash.
            return web.json_response({"error": str(e)}, status=503)

    # handlers --------------------------------------------------------------
    async def index(self, _: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB / "index.html")

    async def status(self, _: web.Request) -> web.Response:
        router = self.app.router
        calls = [
            {**c.to_dict(), "room": router.rooms.get(c.id, {}).get("room")}
            for c in router.calls.values()
        ]
        return web.json_response(
            {
                "phone": self.app.phone.status(),
                "messages": self.app.config.get("messages", {}).get("adapter", "none"),
                "calls": calls,
                "simulate": sorted(k for c in self._simulators() for k in c.simulates),
                "recent": [e.to_dict() for e in self.app.ctx.bus.history][-STATUS_RECENT_EVENTS:],
            }
        )

    async def events(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        )
        await resp.prepare(request)
        try:
            async for event in self.app.ctx.bus.stream():
                await resp.write(
                    f"event: {event.type}\ndata: {json.dumps(event.to_dict())}\n\n".encode()
                )
        except ConnectionResetError:
            pass  # A wait/watch client finished and closed its stream.
        return resp

    async def dial(self, request: web.Request) -> web.Response:
        body = await request.json()
        number = body["number"]
        if not self.app.phone.status().get("connected"):
            raise web.HTTPServiceUnavailable(text="phone not connected")
        # ATD can take seconds, so the dial runs on; its outcome arrives as an
        # event: call.dialing on success, call.failed with this request_id if not.
        request_id = uuid.uuid4().hex[:12]
        self._tasks.spawn(self._dial(request_id, number, body), name=f"http-dial:{request_id}")
        return web.json_response({"ok": True, "request_id": request_id})

    async def _dial(self, request_id: str, number: str, body: dict[str, Any]) -> None:
        try:
            await self.app.router.dial(
                number,
                room=body.get("room", ""),
                agent_name=body.get("agent_name", ""),
                task=body.get("task", ""),
            )
        except RuntimeError as e:  # PhoneCommandError, or the phone dropped meanwhile
            failure = (
                e.as_dict()
                if isinstance(e, PhoneCommandError)
                else {"error": str(e), "command": "dial", "reason": "unavailable"}
            )
            self.app.ctx.bus.publish(E.CALL_FAILED, request_id=request_id, number=number, **failure)

    async def play(self, request: web.Request) -> web.Response:
        body = await request.json()
        call = self._active_call(body.get("call_id"))
        await self.app.router.play_file(
            call,
            body["file"],
            loop=as_bool(body.get("loop")),
            hangup_after=as_bool(body.get("hangup_after"), default=True),
        )
        return web.json_response({"ok": True, "call_id": call.id})

    async def bridge(self, request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else {}
        call = self._active_call(body.get("call_id"))
        info = await self.app.router.route_to_room(call)
        return web.json_response({"ok": True, "call_id": call.id, **info})

    async def send_sms(self, request: web.Request) -> web.Response:
        if self.app.messages is None:
            raise web.HTTPNotImplemented(text="no messages adapter configured")
        body = await request.json()
        await self.app.messages.send(body["to"], body["text"])
        return web.json_response({"ok": True})

    async def _capture_sms(self, event: Event) -> None:
        if event.type in (E.SMS_RECEIVED, E.SMS_SENT):
            self.sms.appendleft(event)

    async def list_sms(self, request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", SMS_DEFAULT_LIMIT))
        if not 1 <= limit <= SMS_KEPT:
            raise ValueError(f"limit must be between 1 and {SMS_KEPT}")
        return web.json_response([e.to_dict() for e in list(self.sms)[:limit]])

    def _directory(self) -> Directory:
        if self.app.directory is None:
            raise web.HTTPNotImplemented(text="no directory adapter configured")
        return self.app.directory

    async def contacts(self, _: web.Request) -> web.Response:
        return web.json_response(await self._directory().contacts())

    async def history(self, _: web.Request) -> web.Response:
        return web.json_response(await self._directory().history())

    async def directory_info(self, _: web.Request) -> web.Response:
        return web.json_response(self._directory().info())

    async def directory_sync(self, _: web.Request) -> web.Response:
        self._directory().sync()
        return web.json_response({"ok": True, **self._directory().info()})

    async def debug(self, _: web.Request) -> web.Response:
        """Everything useful for troubleshooting, in one call."""
        info = getattr(self.app.directory, "info", None)
        return web.json_response(
            {
                "uptime_s": round(time.time() - self.started_at),
                "phone": self.app.phone.status(),
                "directory": info() if info else None,
                "config": {
                    "transport": self.app.config.get("bluetooth", {}).get("transport"),
                    "bridge": self.app.config.get("bridge", {}).get("adapter"),
                    "messages": self.app.config.get("messages", {}).get("adapter"),
                },
                "calls": [c.to_dict() for c in self.app.router.calls.values()],
                "rooms": self.app.router.rooms,
                "events": [e.to_dict() for e in self.app.ctx.bus.history],
                "logs": list(self.logs.lines),
            },
            dumps=lambda o: json.dumps(o, default=str),
        )

    async def phones(self, _: web.Request) -> web.Response:
        fn = getattr(self.app.phone, "phones", None)
        if fn is None:
            return web.json_response([])
        return web.json_response(await fn())

    async def pair(self, request: web.Request) -> web.Response:
        fn = getattr(self.app.phone, "pair", None)
        if fn is None:
            raise web.HTTPNotImplemented(text="this phone adapter has no pairing")
        body = await request.json() if request.can_read_body else {}
        window = await fn(float(body.get("seconds", PAIR_DEFAULT_S)))
        return web.json_response({"ok": True, "discoverable": True, **(window or {})})

    async def forget_phone(self, request: web.Request) -> web.Response:
        fn = getattr(self.app.phone, "forget", None)
        if fn is None:
            raise web.HTTPNotImplemented(text="this phone adapter cannot forget")
        body = await request.json()
        await fn(body["address"])
        return web.json_response({"ok": True})

    async def get_settings(self, _: web.Request) -> web.Response:
        return web.json_response(settings_current(self.app))

    async def set_settings(self, request: web.Request) -> web.Response:
        body = await request.json()
        settings_validate(self.app, body)
        settings_apply(self.app, body)
        saved = settings_current(self.app)
        self.app.settings.save({k: v for k, v in saved.items() if k != "summary"})
        return web.json_response(settings_current(self.app))

    async def token(self, request: web.Request) -> web.Response:
        body = await request.json()
        return web.json_response(
            self.app.bridge.join_info(body["room"], body.get("identity", "web-user"))
        )

    async def simulate(self, request: web.Request) -> web.Response:
        kind = request.match_info["kind"]
        body = await request.json() if request.can_read_body else {}
        for c in self._simulators():
            if kind in c.simulates:
                await c.simulate(kind, **body)
                return web.json_response({"ok": True})
        raise web.HTTPNotFound(text=f"no adapter simulates {kind!r}")

    # helpers ---------------------------------------------------------------
    def _active_call(self, call_id: str | None) -> Call:
        calls = self.app.router.calls
        if call_id:
            if call_id not in calls:
                raise web.HTTPNotFound(text=f"no call {call_id}")
            return calls[call_id]
        if len(calls) != 1:
            raise web.HTTPConflict(text=f"{len(calls)} active calls; pass call_id")
        return next(iter(calls.values()))

    def _simulators(self) -> list[_Simulating]:
        return [c for c in self.app.components() if isinstance(c, _Simulating)]

    def _cmd(self, fn: PhoneCommand) -> Handler:
        async def handler(request: web.Request) -> web.Response:
            body = await request.json() if request.can_read_body else {}
            await fn(self.app.phone, body)
            return web.json_response({"ok": True})

        return handler


class _RingLog(logging.Handler):
    """Last N log lines (INFO+) for GET /api/debug, so an agent can see what
    went wrong without SSH access to the box."""

    def __init__(self, size: int = 500) -> None:
        super().__init__(logging.INFO)
        self.lines: deque[str] = deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "aiohttp.access":
            return
        with contextlib.suppress(Exception):
            self.lines.append(self.format(record))
