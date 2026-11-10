"""Send an AI to sit through a phone menu for you, built on agent.py.

The agent dials, walks the menu (keys and short spoken answers, by rules from
the call profile or by the model), types verification codes, sits through
hold music and explains the request to a voice bot. It talks to you through
Agent Phone while it works:

- it asks: `ask_owner` raises a question card; your typed answer comes back
  as the tool's result, nothing is said on the line;
- it calls you in: when a live person picks up, Agent Phone jumps to the
  front with a red frame, a sound and "A person is on the line". You turn
  your mic on and talk (or, with on_person = "agent", it talks for you);
- you steer: the app's Say box puts your words on the line in its voice.

Everything it hears and does is appended to calls/live.log:

    tail -f calls/live.log

See ivr_menu.py for the call profile and README.md for a walk-through.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from agent import DTMF, PHONE, PhoneAgent, answered_in_time, place_call, report, serve_owner
from ivr_menu import (
    CODE_DIGITS,
    CODE_PROMPT,
    LOCK_KEYS_UNTIL,
    MENU_HINTS,
    MENU_WORDS,
    MIN_PERSON_WORDS,
    ON_PERSON,
    OWNER,
    RULES,
    digits_in,
    identity_text,
    live,
    looks_like_person,
    mask,
    plain,
    standing_text,
)
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    function_tool,
    get_job_context,
    room_io,
)
from livekit.plugins import openai
from owner_tools import OwnerTools

if TYPE_CHECKING:
    from collections.abc import AsyncIterable

    from livekit import rtc
    from livekit.agents import ConversationItemAddedEvent, JobContext, ModelSettings

log = logging.getLogger("ivr-agent")

AGENT_NAME = os.getenv("AGENT_NAME", "ivr-agent")  # run a second worker under another name
KEY_GAP = float(os.getenv("KEY_GAP", "0.8"))  # seconds between tones; menus drop fast ones
CODE_RETRY_AFTER = 5  # a re-prompt allows the same code once more after this
PHONE_TAIL = 1.0  # seconds the phone path still buffers after playout ends
NOTIFIER = shutil.which("terminal-notifier")  # optional desktop banner on macOS


def person_instructions(task: str) -> str:
    return (
        f"You are on a phone call with a person, calling on behalf of {OWNER}.\n"
        f"Goal: {task}\n"
        f"{standing_text()}"
        "If they ask something you don't know, call ask_owner and say "
        "'one moment please' while you wait. Never guess personal facts.\n"
        "Speak English.\n"
        "Confirm names, dates and reference numbers back. One or two short "
        "spoken sentences at a time."
    )


def ivr_instructions(task: str) -> str:
    hints = f"Known menu facts: {MENU_HINTS}\n" if MENU_HINTS else ""
    return (
        f"You are on a phone call to an automated phone system on behalf of {OWNER}.\n"
        f"Goal: {task}\n"
        f"Get through the menu to a live human representative, then hand the call to {OWNER}.\n"
        "Rules:\n"
        "- English only, whatever language the menu offers or speaks. Never press or say "
        "a language option ('para español', 'for another language').\n"
        "- A menu list is not finished until it goes quiet or says 'to repeat'. Hear "
        "EVERY option before choosing; 'representative' is usually near the end.\n"
        "- Never press 0 unless the menu says 0 reaches a representative.\n"
        "- If you land in a different department's menu, call ask_owner first.\n"
        "- Wait until a prompt finishes before acting. Use press_keys for options.\n"
        "- Your voice is muted. To speak to the system use say_to_menu with a few "
        "exact words. Never comment or narrate to a machine.\n"
        "- Verification codes are entered automatically; ignore them.\n"
        "- If a virtual assistant asks what you need, explain the goal in one or two "
        "short sentences and ask for a representative.\n"
        "- On hold or in a queue say NOTHING and never hang up, however long it takes.\n"
        "- Recorded announcements are not people. A real person introduces themselves "
        "and asks how they can help. Then call human_answered with their exact words.\n"
        "- Only end the call if the system says it is closed or disconnects you.\n"
        "- Anything you don't know: call ask_owner with a short question. Don't guess "
        "personal facts.\n"
        f"{identity_text()}{hints}{standing_text()}"
        "One or two short spoken sentences at a time, no lists or markdown."
    )


def notify(title: str, message: str) -> None:
    """A desktop banner, when terminal-notifier is installed (Agent Phone alerts anyway)."""
    if NOTIFIER is None:
        return
    try:
        subprocess.Popen(  # noqa: S603 - fixed binary, our own text
            [NOTIFIER, "-title", title, "-message", message, "-sound", "Glass", "-ignoreDnD"]
        )
    except OSError:
        log.exception("could not notify the owner")


class IvrAgent(OwnerTools, PhoneAgent):
    """The owner listens from Agent Phone the whole call, so joining the room
    is not a takeover. The agent goes quiet only after a person answers."""

    def __init__(self, *, number: str, task: str) -> None:
        # skip PhoneAgent's caller role and its end-call tool: a menu agent
        # must not hang up on hold music
        self.number, self.name, self.task = number, "", task
        self.unlocked = not LOCK_KEYS_UNTIL
        self.in_queue = self.talking = self.handed_off = False
        self.last_heard, self.last_heard_at, self.last_key_at = "", 0.0, 0.0
        self._voice = False  # TTS gate: only say_to_menu and a live person
        self._rule_speaking = False
        self._codes_sent: set[str] = set()
        self._collecting, self._code_buf = False, ""
        self._retry_code, self._code_at = False, 0.0
        Agent.__init__(
            self,
            instructions="Stay silent. Do nothing. Never call tools."
            if RULES
            else ivr_instructions(task),
        )

    # plumbing ----------------------------------------------------------------
    async def press(self, keys: str) -> None:
        self.last_key_at = asyncio.get_running_loop().time()
        participant = get_job_context().room.local_participant
        for key in keys:
            await participant.publish_dtmf(code=DTMF[key], digit=key)
            await asyncio.sleep(KEY_GAP)

    async def _wait_for_quiet(self, quiet: float = 0.7, limit: float = 20.0) -> None:
        """Menus drop keys pressed while a prompt plays: wait until the other
        side has been silent for `quiet` seconds (at most `limit`)."""
        loop = asyncio.get_running_loop()
        start = last_loud = loop.time()
        while loop.time() - start < limit:
            if self.session.user_state == "speaking":
                last_loud = loop.time()
            elif loop.time() - last_loud >= quiet:
                return
            await asyncio.sleep(0.1)

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        """Drop the model's free talk; voice only when the gate is open."""
        if not self._voice:
            async for _ in text:
                pass
            return
        async for frame in Agent.default.tts_node(self, text, model_settings):
            yield frame

    # what we hear ------------------------------------------------------------
    def heard(self, text: str) -> None:
        """Every sentence from the other side, in order."""
        low = text.lower()
        self.last_heard, self.last_heard_at = text, asyncio.get_running_loop().time()
        if LOCK_KEYS_UNTIL and LOCK_KEYS_UNTIL in low:
            self.unlocked = True
        if "didn" in low and "get that" in low:
            self._retry_code = True
        if self.in_queue and looks_like_person(text):
            self.person_on_line(text)
        said = plain(text)
        for phrases, action in RULES:
            if any(p in said for p in phrases):
                self.spawn(self.run_rule(action, text))
                break
        self.collect_code(text)

    def person_on_line(self, greeting: str) -> None:
        live(f"!!! POSSIBLE PERSON: {greeting[:100]!r}")
        self.in_queue = False
        if ON_PERSON == "agent":
            self.spawn(self.start_talking(greeting))
            return
        notify("A person is on the line", f"{greeting[:80]} — turn your mic on and talk")
        self.call_owner_in(greeting)

    async def run_rule(self, actions: str, heard: str) -> None:
        for action in actions.split("+"):  # e.g. "keys:0+queue"
            kind, _, arg = action.partition(":")
            if kind == "keys":
                await self._wait_for_quiet(quiet=0.6)
                live(f"[rule keys] {arg}  <- {heard[:60]!r}")
                await self.press(arg)
            elif kind == "say":
                self._rule_speaking = True
                try:
                    await self.say_to_menu(arg)
                finally:
                    self._rule_speaking = False
            elif kind == "queue":
                # asking for a person proves nothing: the line may still be full
                self.in_queue = True
                live("~~~ asked for a representative; waiting to hear if a transfer happens")
            elif kind == "hangup":
                live(f"=== rule hangup <- {heard[:60]!r}")
                get_job_context().delete_room()

    def collect_code(self, text: str) -> None:
        """An anti-bot code may come in the same sentence or the next ones."""
        if found := CODE_PROMPT.search(text):
            self._code_buf, self._collecting = "", True
            text = text[found.end() :]
        if not self._collecting:
            return
        self._code_buf += digits_in(text)
        if len(self._code_buf) >= CODE_DIGITS:
            code, self._collecting = self._code_buf[:CODE_DIGITS], False
            self.spawn(self.enter_code(code))

    async def enter_code(self, code: str) -> None:
        """Verification prompts are deterministic: key the code ourselves, once
        per prompt (a re-prompt allows one more try)."""
        now = asyncio.get_running_loop().time()
        if code in self._codes_sent and not (
            self._retry_code and now - self._code_at > CODE_RETRY_AFTER
        ):
            return
        self._retry_code, self._code_at = False, now
        self._codes_sent.add(code)
        await self._wait_for_quiet(quiet=0.6)
        live(f"[auto code] {code}")
        await self.press(code)

    async def start_talking(self, greeting: str) -> None:
        """on_person = "agent": switch from menu mode to a conversation."""
        self.talking = self._voice = True
        await self.update_instructions(person_instructions(self.task))
        self.session.generate_reply(
            instructions=f'A person just said: "{greeting}". Introduce yourself and the reason for the call.'
        )

    # the owner ---------------------------------------------------------------
    async def on_enter(self) -> None:
        live(f"--- call to {self.number} connected; listening to the menu")

    async def owner_joined(self) -> None:
        live("(owner is listening)")

    def owner_left(self) -> None:
        live("(owner stopped listening)")

    def owner_says(self, text: str) -> None:
        """The app's Say box: the answer while ask_owner waits, otherwise words
        to put on the line as they are."""
        if self.take_owner_line(text):
            live(f"<<< OWNER: {text}")
            return
        live(f"[owner line] {text}")
        self.spawn(self._say_for_owner(text))

    async def _say_for_owner(self, text: str) -> None:
        self._rule_speaking = True
        try:
            await self.say_to_menu(text)
        finally:
            self._rule_speaking = False

    # tools -------------------------------------------------------------------
    @function_tool
    async def press_keys(self, digits: str) -> str:
        """Press keys on the call, e.g. to navigate a phone menu.

        Args:
            digits: Keys in order. Allowed: 0-9, * and #.
        """
        keys = digits.strip().upper()
        if not keys or any(k not in DTMF for k in keys):
            return f"Nothing pressed: use only 0-9, * or #. Got {digits!r}."
        if RULES and not self.talking:
            return "Menu navigation is automatic here. Do nothing and stay silent."
        if not self.unlocked:
            live(f"(blocked keys {keys}: menu not past '{LOCK_KEYS_UNTIL}' yet)")
            return "Blocked: do not press anything yet; wait silently."
        await self._wait_for_quiet()
        live(f"[keys] {keys}")
        await self.press(keys)
        return f"Pressed {keys}. Now stay silent and listen."

    @function_tool
    async def say_to_menu(self, words: str) -> str:
        """Speak to the automated system: a menu word ("representative"), a
        yes/no, a name, or digits. Your voice is otherwise muted.

        Args:
            words: Exactly what to say, a few words at most.
        """
        if RULES and not self._rule_speaking and not self.talking:
            live(f"(blocked model speech {words!r}: rules drive this menu)")
            return "Menu navigation is automatic here. Do nothing and stay silent."
        await self._wait_for_quiet()
        live(f"[say] {words}")
        self._voice = True
        try:
            # session.say goes through plain TTS: word for word, no commentary;
            # menus make noise, so no interruptions
            await self.session.say(words, allow_interruptions=False, add_to_chat_ctx=True)
        finally:
            self._voice = False  # free model talk never reaches the line
        return "Said. Now stay silent and listen."

    @function_tool
    async def ask_owner(self, question: str) -> str:
        """Silently ask the owner for a fact you don't have. Blocks until the
        owner answers (up to 3 minutes) and returns the answer text.

        Args:
            question: One short question for the owner.
        """
        live(f"??? ASK: {question}")
        notify("The agent has a question", f"{question} — answer in Agent Phone")
        answer = await self.wait_for_owner(question)
        if answer is None:
            live("<<< no answer from owner")
            return f"{OWNER} did not answer. Ask the person to hold, or move on."
        return f"{OWNER} says: {answer}"

    @function_tool
    async def human_answered(self, their_words: str) -> str:
        """Call this only when a live human representative has greeted you.

        Args:
            their_words: The person's greeting, quoted exactly as you heard it
                (e.g. "Thanks for calling, this is Sam, how can I help?").
        """
        if RULES:
            return "The owner is listening and will take over. Stay silent."
        if self.handed_off:
            return "Already handed off; stay quiet."
        # trust what we heard, not the model's quote: the last thing the other
        # side said must come after our last key press and not be menu talk
        words = self.last_heard.lower()
        if (
            self.last_heard_at <= self.last_key_at
            or len(words.split()) < MIN_PERSON_WORDS
            or MENU_WORDS.search(words)
        ):
            live(f"(not a person: model said {their_words!r}, heard {self.last_heard[:80]!r})")
            return "That is still the automated system. Keep waiting silently."
        self.handed_off = True
        live("!!! LIVE HUMAN")
        notify("A person is on the line", f"{self.number}: turn your mic on and talk")
        self.call_owner_in(self.last_heard)
        self.spawn(self._hold_for_owner())
        return "Handled: the person was asked to hold. Say nothing at all from now on."

    async def _hold_for_owner(self) -> None:
        """Word for word through TTS, then mute only after it has played out:
        a timer cut "...is joining" down to "...is no" once."""
        self._voice = True
        try:
            await self.session.say(
                f"Hi, thank you. One moment please, {OWNER} is joining.",
                allow_interruptions=False,
                add_to_chat_ctx=True,
            )
            await asyncio.sleep(PHONE_TAIL)
        finally:
            self._voice = False
        live("(agent muted; you have the line)")


def _mask_file(path: Path) -> None:
    if path.exists():
        path.write_text(mask(path.read_text()))


server = AgentServer(port=int(os.getenv("AGENT_HTTP_PORT", "8091")))


@server.rtc_session(agent_name=AGENT_NAME)
async def ivr_agent(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}")
    # {"phone_number"} when we dispatch ourselves (we dial);
    # {"number", "task"} when `agent-bluetooth call --agent` dials for us
    dial = meta.get("phone_number") or meta.get("number", "")
    task = meta.get("task", "")

    await ctx.connect()
    live(f"=== call to {dial}")
    if meta.get("phone_number"):
        await place_call(ctx.room.name, dial)
    await ctx.wait_for_participant(identity=PHONE)
    if not await answered_in_time(ctx):
        live("=== no answer")
        ctx.shutdown()
        return

    agent = IvrAgent(number=dial, task=task)
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="marin", modalities=["text"]),
        tts=openai.TTS(voice="marin"),
    )

    @session.on("conversation_item_added")
    def _item(ev: ConversationItemAddedEvent) -> None:
        role, text = getattr(ev.item, "role", ""), getattr(ev.item, "text_content", "")
        if not text or role not in {"user", "assistant"}:
            return
        live(f"{'THEM' if role == 'user' else 'AGENT'}: {text}")
        if role == "user":
            agent.heard(text)

    async def _report() -> None:
        await report(ctx, session, {"number": dial, "task": task})
        await asyncio.to_thread(_mask_file, Path("calls") / f"{ctx.room.name}.json")
        live("=== call ended")

    ctx.add_shutdown_callback(_report)
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(participant_identity=PHONE, delete_room_on_close=True),
    )
    await serve_owner(ctx, agent)


if __name__ == "__main__":
    import sys

    from livekit.agents import cli

    sys.argv = [sys.argv[0], "start"]
    cli.run_app(server)
