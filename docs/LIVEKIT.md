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

## Joining a call yourself

```bash
uv run agent-bluetooth join you     # prints {url, room, token}
```

The Mac app does this when you answer.
