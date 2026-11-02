# LiveKit

Every call becomes a LiveKit room. The phone line joins it as a participant
that behaves like a LiveKit SIP trunk participant, so telephony agents and
tools work without changes.

## The phone participant

- identity `phone`, `kind == PARTICIPANT_KIND_SIP`
- attributes `sip.callID`, `sip.phoneNumber`, `sip.trunkID = agent-bluetooth`,
  and `sip.callStatus`, kept live: `dialing` / `ringing` → `active` → `hangup`
- audio: the line, 48 kHz mono

## DTMF

Any participant's `publish_dtmf()` is dialed on the real call, so
`send_dtmf_events`, `GetDtmfTask` and `ivr_detection` work as they do on SIP.

```python
await ctx.room.local_participant.publish_dtmf(code=1, digit="1")
```

Keys pressed by the other party can't be detected: Bluetooth hands-free
carries audio only.

## Send calls to your agent

```bash
uv run agent-bluetooth settings set incoming=agent agent_name=phone-receptionist
```

Each call opens a room and dispatches the agent registered under that name;
the call (number, direction) is in the dispatch metadata. When the room
empties, the call is hung up.

A complete receptionist agent: [examples/livekit-agent](../examples/livekit-agent).

## Joining a call yourself

```bash
uv run agent-bluetooth join you     # prints {url, room, token}
```

The Mac app does this when you answer.
