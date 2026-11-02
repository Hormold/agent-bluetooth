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


def _caller(ctx: JobContext, participant) -> tuple[str, str]:
    """(number, contact name or ""). agent-bluetooth fills the name from the
    phone's contacts and marks phone.contact=true when the caller is known."""
    attrs = participant.attributes or {}
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except json.JSONDecodeError:
        meta = {}
    number = attrs.get("sip.phoneNumber") or str(meta.get("number") or "unknown")
    known = attrs.get("phone.contact") == "true" or bool(meta.get("contact"))
    name = (attrs.get("phone.name") or meta.get("name") or "") if known else ""
    return number, name


class Receptionist(Agent):
    def __init__(self, caller: str, name: str = "") -> None:
        who = f"This is {name}, someone in the owner's contacts. " if name else ""
        super().__init__(
            instructions=(
                "You are a phone receptionist on a live call. "
                f"The caller's number is {caller}. {who}"
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
        self.name = name

    async def on_enter(self) -> None:
        hello = f"Greet {self.name} by first name." if self.name else "Greet the caller."
        self.session.generate_reply(
            instructions=(
                f"{hello} Say you are the receptionist and ask how you can help. "
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
    caller, name = _caller(ctx, phone)
    log.info("call from %s%s in %s", caller, f" ({name})" if name else "", ctx.room.name)
    session = AgentSession(llm=openai.realtime.RealtimeModel(voice="marin"))
    await session.start(agent=Receptionist(caller, name), room=ctx.room)


if __name__ == "__main__":
    from livekit.agents.__main__ import main

    raise SystemExit(main(["start", "--dev"]))
