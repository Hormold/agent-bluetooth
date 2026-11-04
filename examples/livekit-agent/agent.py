"""A LiveKit voice agent that answers and places calls on your own phone.

agent-bluetooth puts each call in a LiveKit room, with the handset as a SIP
participant (identity "phone", sip.* attributes). So this is a regular LiveKit
telephony agent, following LiveKit's outbound-calls guide:

- Inbound: the daemon opens a room and dispatches this agent. It greets the
  caller (by name when they are in your contacts).
- Outbound: dispatch this agent with {"phone_number": ..., "task": ...}. It asks
  agent-bluetooth to dial into its room (where a SIP trunk would use
  CreateSIPParticipant), waits for sip.callStatus == "active", lets the other
  side speak first, and works on the task.

Every call's transcript is written to ./calls/<room>.json when the call ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    function_tool,
    get_job_context,
    room_io,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import openai

if TYPE_CHECKING:
    from livekit import rtc
    from livekit.agents import (
        JobContext,
    )

load_dotenv()
log = logging.getLogger("phone-agent")

AGENT_NAME = "phone-agent"
PHONE = "phone"  # identity of the handset in every call room
DAEMON = os.getenv("AGENT_BLUETOOTH_URL", "http://127.0.0.1:8080")
OWNER = os.getenv("OWNER_NAME", "the owner")
ANSWER_TIMEOUT = 60  # seconds of ringing before giving up
TRANSCRIPTS = Path("calls")

_DTMF = {str(n): n for n in range(10)} | {
    "*": 10,
    "#": 11,
    "A": 12,
    "B": 13,
    "C": 14,
    "D": 15,
}


class PhoneAgent(Agent):
    """One agent for both directions. Inbound it is a receptionist; outbound it
    calls on the owner's behalf to get `task` done."""

    def __init__(self, *, number: str, name: str = "", task: str = "") -> None:
        self.number, self.name, self.task = number, name, task
        if task:
            role = (
                f"You are calling {number} on behalf of {OWNER}. Your task: {task}\n"
                "They just picked up. Say who you are and why you're calling. "
                "Stay on the task, confirm details back, "
                "and end the call once it's done or clearly can't be done. "
                "If you reach voicemail, leave one short message and end the call."
            )
        else:
            who = f" It's {name}, from {OWNER}'s contacts." if name else ""
            role = f"You answer {OWNER}'s phone. The caller is {number}.{who}"
        super().__init__(
            instructions=(
                f"{role}\nYou are on a live phone call: one or two short spoken "
                "sentences at a time, no lists or markdown. Use press_keys for "
                "phone menus."
            ),
            tools=[
                EndCallTool(
                    delete_room=True,  # the daemon hangs up once the room is empty
                    end_instructions="Say one short goodbye.",
                )
            ],
        )

    async def on_enter(self) -> None:
        if self.task:
            # Outbound: give them a moment to say "hello?" first.
            await asyncio.sleep(1.5)
            if self.session.user_state != "speaking" and not self._heard_them():
                self.session.generate_reply(
                    instructions="Introduce yourself and say why you're calling."
                )
            return
        hello = f"Greet {self.name} by first name" if self.name else "Greet the caller"
        self.session.generate_reply(
            instructions=f"{hello}, say you're {OWNER}'s assistant, ask how you can help."
        )

    def _heard_them(self) -> bool:
        return any(getattr(i, "role", None) == "user" for i in self.session.history.items)

    @function_tool
    async def press_keys(self, digits: str) -> str:
        """Press keys on the call, e.g. to navigate a phone menu.

        Args:
            digits: Keys in order. Allowed: 0-9, * and #.
        """
        keys = digits.strip().upper()
        if not keys or any(k not in _DTMF for k in keys):
            return f"Nothing pressed: use only 0-9, * or #. Got {digits!r}."
        room = get_job_context().room
        for key in keys:
            await room.local_participant.publish_dtmf(code=_DTMF[key], digit=key)
            await asyncio.sleep(0.3)
        return f"Pressed {keys}."


async def place_call(room: str, number: str) -> None:
    """agent-bluetooth's stand-in for CreateSIPParticipant: dial `number` on the
    phone and join the line into `room` as participant "phone"."""
    async with (
        aiohttp.ClientSession() as http,
        http.post(f"{DAEMON}/api/calls", json={"number": number, "room": room}) as resp,
    ):
        if resp.status != HTTPStatus.OK:
            raise RuntimeError(f"agent-bluetooth: {resp.status} {await resp.text()}")


async def wait_until_answered(ctx: JobContext) -> bool:
    """Like wait_until_answered=True: True once the callee picks up, False on
    hangup. The caller bounds the wait with asyncio.timeout."""
    phone = await ctx.wait_for_participant(identity=PHONE)
    status: asyncio.Queue[str] = asyncio.Queue()

    @ctx.room.on("participant_attributes_changed")
    def _changed(changed: dict[str, str], p: rtc.Participant) -> None:
        if p.identity == PHONE and "sip.callStatus" in changed:
            status.put_nowait(changed["sip.callStatus"])

    current = phone.attributes.get("sip.callStatus", "")
    try:
        while current not in ("active", "hangup"):
            current = await status.get()
    finally:
        ctx.room.off("participant_attributes_changed", _changed)
    return current == "active"


def save_transcript(ctx: JobContext, session: AgentSession, info: dict) -> None:
    async def write() -> None:
        path = TRANSCRIPTS / f"{ctx.room.name}.json"
        data = json.dumps({**info, **session.history.to_dict()}, indent=2)
        await asyncio.to_thread(_write_file, path, data)
        log.info("transcript: %s", path)

    ctx.add_shutdown_callback(write)


def _write_file(path: Path, data: str) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(data)


async def answered_in_time(ctx: JobContext) -> bool:
    try:
        async with asyncio.timeout(ANSWER_TIMEOUT):
            return await wait_until_answered(ctx)
    except TimeoutError:
        return False


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def phone_agent(ctx: JobContext) -> None:
    # Dispatch metadata. Ours, to place a call: {"phone_number", "task"}.
    # From the daemon: the call itself ({"number", "direction", "task", ...}).
    meta = json.loads(ctx.job.metadata or "{}")
    dial = meta.get("phone_number")
    task = meta.get("task", "")

    await ctx.connect()
    if dial:
        await place_call(ctx.room.name, dial)
    phone = await ctx.wait_for_participant(identity=PHONE)
    attrs = phone.attributes
    number = dial or attrs.get("sip.phoneNumber") or meta.get("number") or "unknown"

    if dial or attrs.get("phone.direction") == "out":
        if not await answered_in_time(ctx):
            log.info("%s didn't answer", number)
            ctx.shutdown()
            return
        log.info("%s answered", number)
        agent = PhoneAgent(number=number, task=task or f"Call {number}.")
    else:
        known = attrs.get("phone.contact") == "true"
        log.info("call from %s", number)
        agent = PhoneAgent(number=number, name=attrs.get("phone.name", "") if known else "")

    # Started once someone's on the line, as LiveKit's outbound guide advises.
    session = AgentSession(llm=openai.realtime.RealtimeModel(voice="marin"))
    save_transcript(ctx, session, {"number": number, "task": agent.task})
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=PHONE,
            delete_room_on_close=True,  # session ends for any reason -> line hangs up
        ),
    )


if __name__ == "__main__":
    from livekit.agents.__main__ import main

    raise SystemExit(main(["start", "--dev"]))
