"""Phone receptionist for rooms opened by agent-bluetooth.

The handset joins each call as participant "phone" (kind SIP) with
sip.phoneNumber. This worker is dispatched by name. end_call deletes the
room; the daemon hangs up the phone once the room is empty.
"""

from __future__ import annotations

import asyncio
import json
import logging

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    function_tool,
    get_job_context,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import openai

load_dotenv()
log = logging.getLogger("phone-receptionist")

AGENT_NAME = "phone-receptionist"
_DTMF = {str(n): n for n in range(10)} | {
    "*": 10,
    "#": 11,
    "A": 12,
    "B": 13,
    "C": 14,
    "D": 15,
}


def _caller_number(ctx: JobContext, participant) -> str:
    number = (participant.attributes or {}).get("sip.phoneNumber") or ""
    if number:
        return number
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except json.JSONDecodeError:
        meta = {}
    return str(meta.get("number") or "unknown")


class Receptionist(Agent):
    def __init__(self, caller: str) -> None:
        super().__init__(
            instructions=(
                "You are a phone receptionist on a live call. "
                f"The caller's number is {caller}. "
                "Greet them, then answer in one or two short spoken sentences. "
                "No markdown, lists, or emojis. "
                "Use press_keys to dial tones on this call, such as an IVR menu. "
                "Use end_call when they are done or ask you to hang up."
            ),
            tools=[
                EndCallTool(
                    delete_room=True,
                    ignore_on_enter=True,
                    end_instructions="Say one short goodbye.",
                    extra_description=(
                        "Hangs up the phone. The line drops once this room is empty."
                    ),
                )
            ],
        )
        self.caller = caller

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=(
                f"Greet the caller. Their number is {self.caller}. "
                "Say you are the receptionist and ask how you can help. "
                "One short sentence."
            )
        )

    @function_tool
    async def press_keys(self, digits: str) -> str:
        """Send DTMF tones on the live phone call.

        Args:
            digits: Keys to press, in order. Allowed: 0-9, *, #, A-D.
        """
        keys = digits.strip().upper()
        bad = [c for c in keys if c not in _DTMF]
        if not keys or bad:
            return f"No tones sent. Use only 0-9, *, #, or A-D. Got {digits!r}."
        room = get_job_context().room
        for key in keys:
            await room.local_participant.publish_dtmf(code=_DTMF[key], digit=key)
            await asyncio.sleep(0.3)
        return f"Pressed {keys}."


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def phone_receptionist(ctx: JobContext) -> None:
    phone = await ctx.wait_for_participant(identity="phone")
    caller = _caller_number(ctx, phone)
    log.info("call from %s in %s", caller, ctx.room.name)
    session = AgentSession(llm=openai.realtime.RealtimeModel(voice="marin"))
    await session.start(agent=Receptionist(caller), room=ctx.room)


if __name__ == "__main__":
    from livekit.agents.__main__ import main

    raise SystemExit(main(["start", "--dev"]))
