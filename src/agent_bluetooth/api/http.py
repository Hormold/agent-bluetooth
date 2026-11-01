"""HTTP + SSE API. Small on purpose: agents can drive the phone with curl."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from ..core.ports import Simulator

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"


class HttpApi:
    def __init__(self, app: App, cfg: dict[str, Any]) -> None:
        self.app = app
        self.host = cfg.get("host", "0.0.0.0")
        self.port = int(cfg.get("port", 8080))
        self.api_key = cfg.get("api_key", "")
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        server = web.Application(middlewares=[self._auth])
        r = server.router
        r.add_get("/", lambda _: web.FileResponse(WEB / "index.html"))
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
        r.add_post("/api/token", self.token)
        r.add_post("/api/simulate/{kind}", self.simulate)
        self._runner = web.AppRunner(server)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()
        log.info("http api on http://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _auth(self, request: web.Request, handler):
        if self.api_key and request.path.startswith("/api/"):
            given = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if (given or request.query.get("key")) != self.api_key:
                raise web.HTTPUnauthorized(text="bad api key")
        return await handler(request)

    # handlers --------------------------------------------------------------
    async def status(self, _: web.Request) -> web.Response:
        router = self.app.router
        calls = [
            {**c.to_dict(), "room": router.rooms.get(c.id, {}).get("room")}
            for c in router.calls.values()
        ]
        return web.json_response({
            "phone": self.app.phone.status(),
            "messages": self.app.config.get("messages", {}).get("adapter", "none"),
            "calls": calls,
            "simulate": sorted(k for c in self._simulators() for k in c.simulates),
            "recent": [e.to_dict() for e in self.app.ctx.bus.history][-50:],
        })

    async def events(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        async for event in self.app.ctx.bus.stream():
            await resp.write(f"event: {event.type}\ndata: {json.dumps(event.to_dict())}\n\n".encode())
        return resp

    async def dial(self, request: web.Request) -> web.Response:
        body = await request.json()
        asyncio.create_task(self.app.phone.dial(body["number"]))
        return web.json_response({"ok": True})

    async def play(self, request: web.Request) -> web.Response:
        body = await request.json()
        call = self._active_call(body.get("call_id"))
        await self.app.router.play_file(call, body["file"], body.get("loop", False), body.get("hangup_after", True))
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

    async def token(self, request: web.Request) -> web.Response:
        body = await request.json()
        return web.json_response(self.app.bridge.join_info(body["room"], body.get("identity", "web-user")))

    async def simulate(self, request: web.Request) -> web.Response:
        kind = request.match_info["kind"]
        body = await request.json() if request.can_read_body else {}
        for c in self._simulators():
            if kind in c.simulates:
                await c.simulate(kind, **body)
                return web.json_response({"ok": True})
        raise web.HTTPNotFound(text=f"no adapter simulates {kind!r}")

    # helpers ---------------------------------------------------------------
    def _active_call(self, call_id: str | None):
        calls = self.app.router.calls
        if call_id:
            if call_id not in calls:
                raise web.HTTPNotFound(text=f"no call {call_id}")
            return calls[call_id]
        if len(calls) != 1:
            raise web.HTTPConflict(text=f"{len(calls)} active calls; pass call_id")
        return next(iter(calls.values()))

    def _simulators(self) -> list[Any]:
        return [c for c in self.app.components() if isinstance(c, Simulator) and hasattr(c, "simulates")]

    def _cmd(self, fn):
        async def handler(request: web.Request) -> web.Response:
            body = await request.json() if request.can_read_body else {}
            await fn(self.app.phone, body)
            return web.json_response({"ok": True})
        return handler
