"""The deterministic half of the IVR agent: call profiles, menu rules, spoken
digits, masking and the live log. No LiveKit here, so it is easy to read and
to reuse.

A profile (CALL_PROFILE=profiles/x.json) describes one kind of call:

    number           the line to dial
    owner_name       who the agent calls on behalf of
    on_person        "handoff" (alert the owner) or "agent" (the agent talks)
    lock_keys_until  no keys until the menu says this phrase
    menu_rules       [[phrases, action], ...]: walk a known menu without the model
    menu_hints       what you learned about the menu, in plain words
    standing_answers question -> answer, so the agent never has to ask
    secrets          label -> env var: identity details the menu may ask for.
                     Values live only in the worker's environment, never in the
                     profile, the dispatch metadata, events or summaries.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

LIVE_LOG = Path("calls/live.log")
MIN_PERSON_WORDS = 4  # shorter phrases are menu fragments, not a greeting
CODE_DIGITS = 4  # anti-bot codes read out by menus
SHOW_LAST = 4  # masked secrets keep their last digits, like a card number
MIN_SHOWN_LEN = 8  # shorter secrets are hidden completely


def load_profile() -> dict:
    path = os.getenv("CALL_PROFILE")
    return json.loads(Path(path).read_text()) if path else {}


PROFILE = load_profile()
OWNER = os.getenv("OWNER_NAME") or PROFILE.get("owner_name") or "the owner"
ON_PERSON = os.getenv("ON_PERSON") or PROFILE.get("on_person", "handoff")
LOCK_KEYS_UNTIL = str(PROFILE.get("lock_keys_until", "")).lower()
MENU_HINTS = os.getenv("MENU_HINTS") or PROFILE.get("menu_hints", "")
STANDING: dict[str, str] = PROFILE.get("standing_answers", {})
SECRETS: dict[str, str] = {
    label: os.getenv(env, "") for label, env in PROFILE.get("secrets", {}).items()
}


def plain(text: str) -> str:
    """Lowercase, punctuation to spaces: rules match words, not formatting."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def load_rules() -> list[tuple[list[str], str]]:
    """menu_rules: [phrases, action]. Phrases are words the menu says,
    alternatives split by "|"; the first rule whose phrase is heard wins.
    Actions: "keys:3", "say:words", "queue" (asked for a person), "hangup",
    joined with "+". Known menus are walked by rules; the model fills gaps."""
    raw = json.loads(os.getenv("MENU_RULES", "null")) or PROFILE.get("menu_rules", [])
    return [([plain(p) for p in phrases.split("|")], action) for phrases, action in raw]


RULES = load_rules()

MENU_WORDS = re.compile(
    r"\b(press|say|menu|option|hold|please wait|transfer|your call|recorded|"
    r"monitored|survey|para|estimated|wait time|didn.t get that)\b"
)
LINE_FULL = re.compile(r"more calls than|unable to assist|try again later", re.IGNORECASE)
CODE_PROMPT = re.compile(r"verification code", re.IGNORECASE)
_DIGIT_WORDS = {
    w: str(i)
    for i, w in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
    )
}
_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_SPOKEN_RUN = re.compile(rf"\b{_WORD}(?:[\s.,-]+{_WORD}){{4,}}\b", re.IGNORECASE)


def digits_in(text: str) -> str:
    """'1234', 'one two three four' and 'onetwothreefour' all give '1234'."""
    t, out, i = text.lower(), [], 0
    while i < len(t):
        if t[i].isdigit():
            out.append(t[i])
            i += 1
            continue
        word = next((w for w in _DIGIT_WORDS if t.startswith(w, i)), None)
        if word:
            out.append(_DIGIT_WORDS[word])
            i += len(word)
        else:
            i += 1
    return "".join(out)


def looks_like_person(text: str) -> bool:
    """A greeting after the queue: long enough, no menu words, not a refusal."""
    return (
        len(text.split()) >= MIN_PERSON_WORDS
        and not MENU_WORDS.search(text.lower())
        and not LINE_FULL.search(text)
    )


def _shown(secret: str) -> str:
    return f"***{secret[-SHOW_LAST:]}" if len(secret) >= MIN_SHOWN_LEN else "***"


def mask(text: str) -> str:
    """Identity details never reach a log or a transcript in full."""
    text = _SPOKEN_RUN.sub("[digits]", text)  # five or more digits said as words
    for secret in filter(None, SECRETS.values()):
        text = text.replace(secret, _shown(secret))
    return text


def live(line: str) -> None:
    """One line of the call, for `tail -f calls/live.log`."""
    LIVE_LOG.parent.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).astimezone().strftime("%H:%M:%S")
    with LIVE_LOG.open("a") as f:
        f.write(f"{stamp} {mask(line)}\n")


def standing_text() -> str:
    if not STANDING:
        return ""
    pairs = "; ".join(f"{q}: {a}" for q, a in STANDING.items())
    return f"Standing answers, use without asking: {pairs}.\n"


def identity_text() -> str:
    known = [f"{label}: {value}" for label, value in SECRETS.items() if value]
    if not known:
        return ""
    return (
        "\nIdentity details, only when the menu asks for them: "
        + "; ".join(known)
        + "\nPrefer the keypad for long numbers; say them only if keys fail.\n"
    )
