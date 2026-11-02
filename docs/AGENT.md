# Driving it from an agent

One daemon owns the radio + LiveKit bridge + HTTP API. Everything is JSON. Drive
it with the CLI (same as `agent-browser`) or hit the API directly.

```
agent-bluetooth watch [phone call room sms]   # live event stream, one JSON/line
agent-bluetooth status                         # phone + current calls
agent-bluetooth call <number>                  # place a call
agent-bluetooth answer | hangup
agent-bluetooth play <file.wav> | bridge       # answer + play, or → LiveKit room
agent-bluetooth join [identity]                # -> {url, token} to enter the room
agent-bluetooth sms <to> <text...>
agent-bluetooth guide                          # full recipe
```

HTTP (default `http://<box>:8080`): `GET /api/status`, `GET /api/events` (SSE),
`POST /api/calls {number}`, `/api/calls/answer|hangup|bridge`, `/api/sms`,
`/api/token {room,identity}`, `GET /api/contacts`, `/api/calls/history`,
`GET|POST /api/settings`, `GET /api/phones`, `POST /api/pair`.

## Events

`phone.connected|disconnected|status`, `call.ringing|dialing|active|ended`,
`call.audio {up}`, `room.opened|joined|left|closed`, `sms.received|sent`,
`recording.saved`, `playback.done`. Parse them; don't scrape text.

## Typical agent loop

```bash
agent-bluetooth watch call | while read -r ev; do
  [ "$(jq -r .type <<<"$ev")" = call.ringing ] || continue
  num=$(jq -r .data.call.number <<<"$ev")
  case "$num" in
    +1800*) agent-bluetooth hangup ;;        # block spam
    *)      agent-bluetooth bridge ;;        # take it into a LiveKit room
  esac
done
```

## Let a LiveKit agent handle calls

Set `agent` mode + `agent_name` (settings or `/api/settings`). Every call opens a
room and dispatches your registered LiveKit agent; the call's phone number
arrives in the dispatch metadata. Your agent talks to the caller over the room.

### It looks like a LiveKit SIP participant

The phone joins each call room exactly like a LiveKit SIP trunk participant, so
telephony agents and tools work unchanged:

- `participant.kind == PARTICIPANT_KIND_SIP`, identity `phone`
- attributes `sip.callID`, `sip.phoneNumber`, `sip.trunkID = agent-bluetooth`, and
  `sip.callStatus` kept live: `dialing` / `ringing` → `active` → `hangup`
- **DTMF:** any participant's `publish_dtmf(code=…, digit=…)` is sent to the phone
  line (`AT+VTS`). So `send_dtmf_events`, `GetDtmfTask` and `ivr_detection` just work.

```python
@ctx.room.on("participant_attributes_changed")
def changed(attrs, p):
    if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP and attrs.get("sip.callStatus") == "active":
        ...  # callee picked up
await ctx.room.local_participant.publish_dtmf(code=1, digit="1")   # press 1 on the call
```

(Hearing DTMF the *remote* side presses isn't available: Bluetooth hands-free
carries only audio, not key events.)

## Automations (no agent, in config)

```toml
[[automations]]
on = "sms.received"
extract = { code = "(\\d{4,8})" }    # regex, not semantic parsing
do = "telegram"
text = "🔐 {{code}} from {{sender}}"
```
Actions: `log shell webhook telegram sms hangup answer play bridge`.
