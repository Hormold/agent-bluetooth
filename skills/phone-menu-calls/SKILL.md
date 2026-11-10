---
name: phone-menu-calls
description: Use when the user needs a call that goes through a phone menu (IVR) and a hold queue to reach a person — a government agency, bank, insurer, utility, airline — through agent-bluetooth and ivr_agent.py. Covers the intake questions, the per-line profile, exploratory calls, reading the transcript, writing menu rules, redialing a full queue, asking the owner for missing facts, and handing the person over.
---

# Calls through phone menus

Every line is different, and each one needs a little tuning. The loop below
gets a new line from "never called" to "the agent walks the menu in one go"
in two or three calls. Each line's tuning is saved as a profile, so the next
call to it needs no tuning.

Files, all in `examples/livekit-agent/`:

| File | What it is |
| --- | --- |
| `ivr_agent.py` | the call agent (LiveKit worker, name `ivr-agent`; `AGENT_NAME` runs a second one) |
| `ivr_menu.py` | profile loading, menu rules, masking, the live log |
| `owner_tools.py` | `ask_owner` and the Agent Phone alert card, for any LiveKit agent ([docs/OWNER.md](../../docs/OWNER.md)) |
| `ivr.sh` | every step as one command: `worker`, `call`, `redial`, `log`, `answer`, `say`, `keys` |
| `profiles/local/<line>.json` | one line: number, menu rules, standing answers, notes; `_template.json` to start |
| `profiles/example-utility.json` | a worked example for a made-up utility line |
| `profiles/local/` | real lines (gitignored): real numbers and names stay out of git |
| `calls/live.log` | everything heard and done, live, one line per event |
| `calls/<room>.json` | the full transcript of each call |

You (Claude) do the thinking between calls: read the log, decide, edit the
profile. The call agent only follows the profile. Don't write parsers for the
log; read it.

## 1. Intake: ask once, before the first call

Ask the owner everything at once, in one message, and skip what you already
know from memory or documents:

1. **Number and line.** Which number, which department/program. Hours and time zone.
2. **Goal.** What has to happen by the end of the call (one sentence). This is the `--task`.
3. **Who is calling.** The account holder's name as the line knows it.
4. **Identity the menu will ask for.** SSN, date of birth, account / claim / policy
   number, ZIP, PIN. Collect secrets through the secret prompt (`request_secret`,
   file delivery), never in chat. They go into env vars listed in `secrets`, never
   into the profile, the logs, or git.
5. **Standing answers.** Yes/no questions the menu or a person is likely to ask
   (are you the account holder, is this about a new order…). Saves asking mid-call.
6. **When a person answers.** `handoff` (owner talks; default) or `agent` (the agent
   talks and asks the owner through `ask_owner` when stuck).
7. **Deadline.** Anything that makes waiting expensive (lines close at 5, a fee kicks in tomorrow, a form is due).

## 2. Profile

Copy `profiles/_template.json` to `profiles/local/<line>.json`. Fill `number`,
`owner_name`, `on_person`, `secrets` (what the menu calls each detail -> the env var holding it), `standing_answers`. Leave
`menu_rules` empty and put what you know in `menu_hints`.

## 3. Exploratory call

```bash
./ivr.sh worker profiles/local/<line>.json        # background; restart it after editing the profile
./ivr.sh call profiles/local/<line>.json "<goal>"
./ivr.sh log                                # follow it
```

With no rules the model walks the menu with `press_keys` and `say_to_menu`.
Its own talk never reaches the line (the voice is gated); only those two tools
make sound. Watch the log. Step in only when it's clearly going wrong:
`./ivr.sh keys 3`, `./ivr.sh say "representative"`, `agent-bluetooth hangup`.
Stepping in while a prompt is still playing costs a try (see lessons).

## 4. Read the call, write the rules

Read `calls/live.log` for that call. For each prompt note what it offered,
what was pressed, and what came next. Then edit the profile:

- **`menu_rules`**: one rule per decision on the good path, as
  `["phrase the menu says|another wording", "action"]`. Phrases are plain
  words, matched case- and punctuation-insensitively, first rule wins. Put
  the rule for the most specific prompt first. Actions: `keys:3`, `say:words`,
  `queue` (we asked for a person), `hangup`, joined with `+`
  (`keys:0+queue`). Add common mishearings as alternatives
  (`say billing|stay billing|say billion`).
- **`lock_keys_until`**: a phrase heard before the first real choice (e.g.
  `verification code`). Keys are refused until then, which keeps the model off
  language menus.
- **`menu_hints`**: dead ends in plain words ("the status option replays a recording
  and loops", "0 at the top transfers to another department").
- **`notes`**: hours, the path, how the line behaves, the date you learned it.

Restart the worker, call again. With rules set, the model has no tools in
the menu at all; the rules walk it. Usually two or three calls get the menu
clean. When a call fails, the log says where. Fix that rule and nothing else.

## 5. A full queue

Many lines answer "more calls than we can answer" and hang up. That is not a
menu bug. Redial:

```bash
./ivr.sh redial profiles/local/<line>.json "<goal>" 10 16:55   # every 10 min until 16:55
```

It stops when a person may be on the line, waits `minutes` when the line is
full, and retries in 30 s when the menu failed. Best odds: the minute the
lines open, and mid-week.

## 6. A person answers

Asking for a representative proves nothing. Only a person actually talking
does. When the agent hears speech after `queue` that isn't the menu, it logs
`!!! POSSIBLE PERSON` and Agent Phone jumps to the front with a red frame, a sound and
"A person is on the line". Then either:

- `handoff`: the owner turns on the mic in Agent Phone and talks. The agent stays silent.
- `agent`: the agent introduces itself and works the goal. For facts it
  lacks it calls `ask_owner`. Agent Phone shows a question card; the owner types
  the answer there, or you answer with `./ivr.sh answer "…"` (same thing as the card).

Never tell the owner "a person is on" from a key press or a rule firing. Say
only what the log shows.

## 7. After the call

- Tell the owner the outcome: what was said, reference numbers, the next step and its date.
- Put what you learned into the profile `notes` with the date.
- The masked transcript stays in `calls/`. Check it for anything sensitive that slipped through.
- Recording: many lines refuse consent to being recorded. Keep `record_calls` off
  for them (`agent-bluetooth settings set record_calls=false`).

## Lessons that hold on most lines

- **Keys pressed while a prompt plays are dropped.** The agent waits for
  silence before tones. Don't fire `./ivr.sh keys` mid-prompt.
- **Anti-bot codes** ("enter the following verification code: 1234") are
  entered automatically. The menu may say the digits as words or in the next
  sentence. Three misses and the line hangs up.
- **The model must not talk to machines.** Free speech ("okay, let's see")
  is heard as an answer and breaks the menu. That's why the voice is gated
  and rules replace the model on known lines.
- **Spoken words get clipped and interrupted.** Prefer keys. Use `say:` only
  where the menu has no key.
- **"Press 0" at the wrong level transfers you** to another department.
  Get to the program's own submenu first.
- **Language menus.** "Press 2 for Spanish" / "press 3 for another language"
  come first. Never react; English is the default. Use `lock_keys_until`. Some
  lines remember a language picked once and answer the next call in it.
- **The owner can't hear the call unless they're in the room.** Agent Phone
  joins as a listener. Mic on means they are on the line.
