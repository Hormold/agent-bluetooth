# Phone agent

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
