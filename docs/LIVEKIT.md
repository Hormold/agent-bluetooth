# LiveKit

Every call becomes a LiveKit room. The phone line joins it as a participant
that behaves like a LiveKit SIP trunk participant, so telephony agents and
tools work without changes.

## The phone participant

- identity `phone`, `kind == PARTICIPANT_KIND_SIP`
- attributes `sip.callID`, `sip.phoneNumber`, `sip.trunkID = agent-bluetooth`,
  and `sip.callStatus`, kept live: `dialing` / `ringing` → `active` → `hangup`
- `phone.name` and `phone.contact` (`true` when the number is in the phone's
  contacts; the name comes from there), so an agent can greet callers by name
- audio: the line, 48 kHz mono

## DTMF

Any participant's `publish_dtmf()` is dialed on the real call, so
`send_dtmf_events`, `GetDtmfTask` and `ivr_detection` work as they do on SIP.

```python
await ctx.room.local_participant.publish_dtmf(code=1, digit="1")
```

Keys pressed by the other party can't be detected: Bluetooth hands-free
carries audio only.

## Have your agent place a call

Same flow as LiveKit's [outbound calls](https://docs.livekit.io/telephony/making-calls/outbound-calls/),
with the daemon in place of a SIP trunk. Dispatch the agent; it asks the
daemon to dial into its room:

```bash
curl -X POST http://127.0.0.1:8080/api/calls -H 'content-type: application/json' \
  -d '{"number": "+14155550142", "room": "<the agent's room>"}'
```

The phone joins as `phone` with `sip.callStatus = dialing` and flips to
`active` when someone picks up. Start the agent session then.

Or let the daemon open the room and dispatch the agent, with the task in the
dispatch metadata (`number`, `direction: "out"`, `task`):

```bash
agent-bluetooth call +14155550142 --agent phone-agent --task "Confirm my 3pm appointment"
```

## Send calls to your agent

```bash
agent-bluetooth settings set incoming=agent agent_name=phone-agent
```

Each call opens a room and dispatches the agent registered under that name;
the call (number, direction) is in the dispatch metadata. When the room
empties, the call is hung up.

A complete agent for both directions: [examples/livekit-agent](../examples/livekit-agent).

## The owner steps in

Two things the daemon does to the room that a SIP trunk wouldn't:

- **`agent-bluetooth say "..."`** publishes a reliable data message from the
  `phone` participant on topic `owner`: `{"text": "...", "call_id": "..."}`.
  The example agent interrupts itself and says the line. Any agent can do the
  same with `room.on("data_received")`.
- **Take over.** When a participant that is neither `phone` nor an agent
  joins (the Mac app's *Take over*, or `agent-bluetooth join`), the example
  agent announces the owner and disables its audio in and out; when that
  participant leaves and the phone is still there, it takes the call back.
  Nothing in the daemon enforces this; it is how the example agent behaves.

Everything the Mac app understands from an agent (the question card, "a
person is on the line", owner lines as answers) is in [OWNER.md](OWNER.md),
with a mixin for LiveKit agents.

## Reporting how the call went

When its session ends, the example agent posts one or two sentences to the
daemon:

```bash
curl -X POST http://127.0.0.1:8080/api/calls/summary -H 'content-type: application/json' \
  -d '{"room": "<the room>", "outcome": "Booked for 8pm, patio.", "transcript": "/abs/calls/<room>.json"}'
```

`call_id` works in place of `room`; the call may already have ended. The
daemon turns it into a `call.summary` event with the call's number, name,
direction and task, the recording path when `record_calls` is on, and your
`outcome`, `details` and `transcript`. The Telegram sink posts it, webhook
sinks and automations get it like any event, and
`agent-bluetooth wait call.summary` returns it.

## Joining a call yourself

```bash
uv run agent-bluetooth join you     # prints {url, room, token}
```

The Mac app does this when you answer.
