# Phone agents

Two [LiveKit Agents](https://docs.livekit.io/agents/) voice agents that place
and answer calls on your own phone through [agent-bluetooth](../../README.md):

- [agent.py](agent.py), `phone-agent`: a conversation (book a table, ask a question).
- [ivr_agent.py](ivr_agent.py), `ivr-agent`: phone menus, hold queues, and a
  handoff to you when a person picks up. [Below](#phone-menus-ivr_agentpy).

Both talk to you through the Mac app while they work; any agent can, with
[owner_tools.py](owner_tools.py) ([docs/OWNER.md](../../docs/OWNER.md)).

## agent.py

A [LiveKit Agents](https://docs.livekit.io/agents/) voice agent that places
and answers calls on your own phone through
[agent-bluetooth](../../README.md). One file, [agent.py](agent.py), built the
way LiveKit's [outbound calls guide](https://docs.livekit.io/telephony/making-calls/outbound-calls/)
describes. The only difference: where that guide creates a SIP participant
through a trunk, this agent asks agent-bluetooth to dial, and your phone joins
the room as that participant.

## Run

```bash
cp .env.example .env     # OPENAI_API_KEY, OWNER_NAME; LiveKit defaults match `livekit-server --dev`
uv sync
uv run python agent.py   # registers "phone-agent" with your LiveKit server
```

## Make a call

```bash
agent-bluetooth call +14155550142 --agent phone-agent \
  --task "Ask whether they're open on Sunday and until when"
```

or with the LiveKit CLI:

```bash
lk dispatch create --new-room --agent-name phone-agent \
  --metadata '{"phone_number": "+14155550142", "task": "Ask whether they are open on Sunday"}'
```

What happens:

1. The agent gets the dispatch and asks the daemon to dial into its room
   (`POST /api/calls {"number", "room"}`).
2. Your phone joins as participant `phone` with `sip.callStatus = dialing`.
3. The agent waits for `sip.callStatus = active` (someone picked up), then
   starts its session. That's `wait_until_answered` for a Bluetooth line.
4. It gives them a second to say "hello?", introduces itself and works on the
   task. `press_keys` gets it through phone menus; `end_call` hangs up.
5. When the call ends, the transcript lands in `calls/<room>.json`.

No answer within 60 seconds, or the session fails: the room is deleted and
the daemon hangs up the phone.

## Answer calls

```bash
agent-bluetooth settings set incoming=agent agent_name=phone-agent
```

Inbound, it greets the caller, by first name when they're in your contacts
(`phone.name`, `phone.contact`), and takes it from there.

## Change it

- **Voice and model:** `AgentSession(llm=openai.realtime.RealtimeModel(...))`.
  Any LiveKit model works: Gemini Live, or an STT + LLM + TTS pipeline.
- **Tools:** add `@function_tool` methods to `PhoneAgent` (calendar lookup,
  CRM, "text me the result").
- **Hosting:** point `LIVEKIT_URL` at [LiveKit Cloud](https://cloud.livekit.io)
  and the daemon at the same project; the agent can then run anywhere.

## Phone menus: ivr_agent.py

For lines where a menu and a queue stand between you and a person. Files:

| File | |
| --- | --- |
| `ivr_agent.py` | the agent: walks the menu, waits, hands over |
| `ivr_menu.py` | the call profile, menu rules, spoken digits, masking, `calls/live.log` |
| `owner_tools.py` | `ask_owner` and the Agent Phone alert card, reusable in any agent |
| `ivr.sh` | every step as one command |
| `profiles/` | `_template.json`, `example-utility.json`; your real lines go in `profiles/local/` (gitignored) |

### One line, end to end

```bash
cp profiles/_template.json profiles/local/insurer.json   # number, your name, what you know
export CALLER_ACCOUNT=...                                # identity details: env only, named in "secrets"
./ivr.sh worker profiles/local/insurer.json &            # registers "ivr-agent"
./ivr.sh call   profiles/local/insurer.json "Ask why my refund hasn't arrived"
./ivr.sh log                                             # follow it live
```

What you see in Agent Phone while it runs:

1. **The call, with sound.** The app joins as a listener, mic off. Sound
   on/off is yours.
2. **A question card** when the agent is stuck ("What ZIP code is on the
   account?"). Type the answer and press Answer: it goes to the agent, never
   onto the line. From a terminal: `./ivr.sh answer "90210"`.
3. **"A person is on the line"**: the window comes to the front with a red
   frame and a sound, the agent says "one moment please" and goes quiet.
   Press *Turn mic on and talk*.
4. **The Say box**, any time: your words go on the line in the agent's voice.

### Tune it for a line

The first call runs on the model alone. Read `calls/live.log`, then put what
worked into the profile: `menu_rules` (`["phrase the menu says", "keys:3"]`,
`"keys:0+queue"`, `"hangup"`), `lock_keys_until`, `menu_hints`,
`standing_answers`. With rules set, the model stays silent and the rules walk
the menu; two or three calls usually make a line clean. A full queue is not a
menu bug:

```bash
./ivr.sh redial profiles/local/insurer.json "Ask why my refund hasn't arrived" 10 16:55
```

A coding agent can do all of this for you: the
[phone-menu-calls](../../skills/phone-menu-calls/SKILL.md) skill is the recipe.

### Good to know

- The agent's free talk never reaches the line; only keys, `say_to_menu` and
  the hand-over sentence do. Menus hear "okay, let me see" as an answer.
- English only: it never presses a language option. Some lines remember a
  language picked once.
- `on_person: "agent"` lets the agent talk to the person itself and ask you
  through the question card when it lacks a fact.
- Several profiles at once: run another worker with `AGENT_NAME=ivr-2` and
  `AGENT_HTTP_PORT=8092`, and dial with `--agent ivr-2`.
