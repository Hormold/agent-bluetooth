# Phone receptionist

A LiveKit voice agent for calls bridged by [agent-bluetooth](../../README.md). Each call is a room. The handset joins as participant `phone` (SIP), with `sip.phoneNumber` and `sip.callStatus`. This worker registers as `phone-receptionist`, greets that number, answers briefly, and has two tools:

- `press_keys(digits)` sends DTMF with `publish_dtmf` (IVR menus, when this agent is the one that dialed).
- `end_call` says goodbye and deletes the room. The daemon hangs up the phone when the room empties (`hangup_on_empty`, about 3 seconds).

Speech is OpenAI Realtime via `OPENAI_API_KEY`. No key is required to register the worker.

```bash
cd examples/livekit-agent
cp .env.example .env   # set OPENAI_API_KEY; LiveKit defaults match `livekit-server --dev`
uv sync
uv run python -m livekit.agents start agent.py --dev
```

Point the daemon at the same LiveKit server, then:

```bash
agent-bluetooth settings set incoming=agent agent_name=phone-receptionist
```

Same thing over HTTP:

```bash
curl -s -X POST http://127.0.0.1:8080/api/settings \
  -H 'content-type: application/json' \
  -d '{"incoming":"agent","agent_name":"phone-receptionist"}'
```

`livekit-server --dev --bind 127.0.0.1 --node-ip 127.0.0.1` is the local server (port 7880, key `devkey`, secret `secret`).
