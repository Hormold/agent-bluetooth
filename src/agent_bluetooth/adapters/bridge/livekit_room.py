"""Each call becomes a LiveKit room. The phone line joins as participant
"phone"; anyone else in the room (you, an agent) is mixed and sent back."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from livekit import api, rtc
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration

from ...core.context import Context
from ...core.events import E
from ...core.ports import PCM_BYTES_PER_SAMPLE, AudioPipe, Call

log = logging.getLogger(__name__)
UPLINK_PREBUFFER_MS = 120
LK_RATE = 48000  # what LiveKit/WebRTC run at natively
LK_FRAME = LK_RATE // 100  # 10 ms


@dataclass
class _Session:
    call: Call
    room: rtc.Room
    name: str
    tracks: dict[str, rtc.Track] = field(default_factory=dict)
    streams: dict[str, AsyncIterator[rtc.AudioFrame]] = field(default_factory=dict)
    mixer: rtc.AudioMixer | None = None
    pipe: AudioPipe | None = None
    tasks: list[asyncio.Task] = field(default_factory=list)
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    # audio-up is announced on dialing AND again when the call goes active; the
    # two attach calls must not interleave or we publish two phone tracks.
    attach_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    publication_sid: str | None = None


class LiveKitBridge:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.bus = ctx.bus
        self.url = cfg.get("url") or os.environ.get(
            "LIVEKIT_URL", "ws://localhost:7880"
        )
        self.client_url = cfg.get("client_url") or self.url  # what browsers use
        self.api_key = cfg.get("api_key") or os.environ.get("LIVEKIT_API_KEY", "devkey")
        self.api_secret = cfg.get("api_secret") or os.environ.get(
            "LIVEKIT_API_SECRET", "secret"
        )
        self.room_prefix = cfg.get("room_prefix", "call-")
        self.identity = cfg.get("identity", "phone")
        self.agent_name = cfg.get("agent_name", "")  # explicit agent dispatch, optional
        self.sessions: dict[str, _Session] = {}

    async def start(self) -> None: ...

    async def stop(self) -> None:
        for s in list(self.sessions.values()):
            await self.close(s.call)

    def join_info(self, room: str, identity: str) -> dict[str, Any]:
        token = self._token(identity, room).with_grants(
            api.VideoGrants(
                room_join=True, room=room, can_publish=True, can_subscribe=True
            )
        )
        return {"url": self.client_url, "room": room, "token": token.to_jwt()}

    async def open(self, call: Call) -> dict[str, Any]:
        digits = re.sub(r"\D", "", call.number)[-10:] or "unknown"
        name = f"{self.room_prefix}{time.strftime('%Y%m%d-%H%M%S')}-{digits}"
        room = rtc.Room()
        s = self.sessions[call.id] = _Session(call=call, room=room, name=name)
        self._wire_events(s)

        token = self._token(self.identity, name).with_name(
            call.name or call.number or "phone"
        )
        token.with_attributes(
            {
                "phone.call_id": call.id,
                "phone.number": call.number,
                "phone.name": call.name,
                "phone.direction": call.direction,
            }
        )
        token.with_grants(api.VideoGrants(room_join=True, room=name))
        if self.agent_name:
            token.with_room_config(
                RoomConfiguration(
                    agents=[
                        RoomAgentDispatch(
                            agent_name=self.agent_name,
                            metadata=json.dumps(call.to_dict()),
                        )
                    ]
                )
            )
        await room.connect(self.url, token.to_jwt())
        s.connected.set()
        log.info("call %s -> room %s", call.id, name)
        return {"room": name, "url": self.client_url}

    async def attach_audio(self, call: Call, audio: AudioPipe) -> None:
        s = self.sessions.get(call.id)
        if s is None:
            # Audio link came up before the room; wait briefly for open() to run.
            for _ in range(50):
                await asyncio.sleep(0.1)
                if (s := self.sessions.get(call.id)) is not None:
                    break
            if s is None:
                return
        async with s.attach_lock:
            await self._attach_locked(s, audio)

    async def _attach_locked(self, s: _Session, audio: AudioPipe) -> None:
        if s.pipe is audio:
            return  # already feeding this exact audio link
        try:  # the room must be fully connected before touching local_participant
            await asyncio.wait_for(s.connected.wait(), timeout=10)
        except TimeoutError:
            log.warning("room %s not connected in time; audio not attached", s.name)
            return
        await self._detach(s)
        s.pipe = audio
        rate = audio.sample_rate
        source = rtc.AudioSource(LK_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("phone-line", source)
        pub = await s.room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        s.publication_sid = pub.sid
        s.mixer = rtc.AudioMixer(rate, 1, blocksize=rate // 50)
        for sid, t in s.tracks.items():
            self._add_stream(s, sid, t)
        s.tasks = [
            asyncio.create_task(self._uplink(audio, source)),
            asyncio.create_task(self._downlink(s.mixer, audio)),
        ]

    async def close(self, call: Call) -> None:
        s = self.sessions.pop(call.id, None)
        if s is None:
            return
        await self._detach(s)
        await s.room.disconnect()
        log.info("room %s closed", s.name)

    # internals -------------------------------------------------------------
    def _token(self, identity: str, room: str) -> api.AccessToken:
        return api.AccessToken(self.api_key, self.api_secret).with_identity(identity)

    def _wire_events(self, s: _Session) -> None:
        room = s.room

        def others() -> int:
            return len(room.remote_participants)

        @room.on("participant_connected")
        def _joined(p: rtc.RemoteParticipant) -> None:
            self.bus.publish(
                E.ROOM_JOINED,
                call_id=s.call.id,
                room=s.name,
                identity=p.identity,
                others=others(),
            )

        @room.on("participant_disconnected")
        def _left(p: rtc.RemoteParticipant) -> None:
            self.bus.publish(
                E.ROOM_LEFT,
                call_id=s.call.id,
                room=s.name,
                identity=p.identity,
                others=others(),
            )

        @room.on("track_subscribed")
        def _sub(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                s.tracks[pub.sid] = track
                if s.mixer:
                    self._add_stream(s, pub.sid, track)

        @room.on("track_unsubscribed")
        def _unsub(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            s.tracks.pop(pub.sid, None)
            stream = s.streams.pop(pub.sid, None)
            if stream and s.mixer:
                s.mixer.remove_stream(stream)

    def _add_stream(self, s: _Session, sid: str, track: rtc.Track) -> None:
        assert s.mixer and s.pipe
        stream = _frames(
            rtc.AudioStream(track, sample_rate=s.pipe.sample_rate, num_channels=1)
        )
        s.streams[sid] = stream
        s.mixer.add_stream(stream)

    async def _detach(self, s: _Session) -> None:
        for t in s.tasks:
            t.cancel()
        s.tasks = []
        if s.mixer:
            await s.mixer.aclose()
            s.mixer = None
        s.streams.clear()
        s.pipe = None
        if s.publication_sid:  # a new audio link gets a fresh track, never a second one
            try:
                await s.room.local_participant.unpublish_track(s.publication_sid)
            except Exception:
                log.debug("unpublish failed", exc_info=True)
            s.publication_sid = None

    @staticmethod
    async def _uplink(pipe: AudioPipe, source: rtc.AudioSource) -> None:
        """Phone -> room, the way LiveKit wants it: 48 kHz, steady 10 ms frames.

        The phone delivers 8 kHz audio in USB-sized bursts of ~3 ms SCO packets.
        Feeding that straight in (odd sizes, native 8 kHz) left a hole in about
        every other 10 ms frame. So: drain the burst, resample to 48 kHz, cut
        exact 10 ms frames. capture_frame blocks on LiveKit's 50 ms buffer, which
        paces the loop in real time; a short prebuffer rides out late bursts."""
        resampler = rtc.AudioResampler(
            pipe.sample_rate,
            LK_RATE,
            num_channels=1,
            quality=rtc.AudioResamplerQuality.MEDIUM,
        )
        step = LK_FRAME * PCM_BYTES_PER_SAMPLE
        prime = step * UPLINK_PREBUFFER_MS // 10
        primed = False
        buf = bytearray()
        closed = False
        while not closed:
            pcm = await pipe.read()
            chunk = bytearray()
            while pcm:
                chunk += pcm
                pcm = pipe.read_nowait()
            closed = pcm is None
            if chunk:
                for f in resampler.push(chunk):
                    buf += f.data.cast("b")
            if not primed and len(buf) < prime and not closed:
                continue
            primed = True
            pipe.bridge_buffer_ms = len(buf) / step * 10
            while len(buf) >= step:
                frame = rtc.AudioFrame(bytes(buf[:step]), LK_RATE, 1, LK_FRAME)
                del buf[:step]
                await source.capture_frame(frame)
        log.info("uplink done; %s", pipe.stats())

    @staticmethod
    async def _downlink(mixer: rtc.AudioMixer, pipe: AudioPipe) -> None:
        async for frame in mixer:
            pipe.write(bytes(frame.data))


async def _frames(stream: rtc.AudioStream) -> AsyncIterator[rtc.AudioFrame]:
    async for event in stream:
        yield event.frame
