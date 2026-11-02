"""Practical recipes printed by agent-bluetooth guide."""

GUIDE = r"""
Drive the running daemon. Global options go BEFORE the command:
  agent-bluetooth --host http://127.0.0.1:8080 --key TOKEN status
Or set AGENT_BLUETOOTH_HOST and AGENT_BLUETOOTH_KEY.
Commands print JSON; watch prints JSON lines. guide, logs and help print text.
Exit codes: 0 success, 1 request failure/wait timeout, 2 invalid CLI arguments.

Inspect before acting:
  agent-bluetooth status
  agent-bluetooth settings
  agent-bluetooth contacts "Anna"
  agent-bluetooth history
  agent-bluetooth sync
Contacts/history require a directory adapter and reflect its last sync.

Send and read SMS:
  agent-bluetooth sms +15551234567 "On my way"
  agent-bluetooth sms list -n 20
GET /api/sms?limit=20 returns newest-first sms.received/sms.sent event objects
{type, data, ts}. Received data has sender/text; sent data has to/text.
Only observed events since startup are available, capped at 1000 messages.
This does not fetch old messages from the phone. MAP may truncate received text
unless fetch_full is enabled; phone permissions/platform may limit SMS support.

Call, wait for the other side to answer, press a key, hang up:
  agent-bluetooth call +15551234567
  agent-bluetooth wait call.active --timeout 60 --match call.number=+15551234567
  agent-bluetooth dtmf '1#'
  agent-bluetooth hangup
wait consumes LIVE SSE events only. Start it before the triggering action if
that action may emit immediately, in another terminal or background process.
It does not replay events; status shows calls already active. Match keys are
relative to data, support dotted paths, and repeated --match filters use AND.
  agent-bluetooth wait sms.received --timeout 60 --match sender=+15551234567
  agent-bluetooth watch call sms

Configure subsequent calls, persisted on the daemon:
  agent-bluetooth settings set incoming=play play_file=/abs/greeting.wav
  agent-bluetooth settings set outgoing=ignore agent_name=receptionist answer=on_join
  agent-bluetooth settings set incoming=ignore
play_file must exist on the DAEMON machine: absolute path, nonempty mono/stereo
16-bit PCM WAV. Play answers incoming calls immediately, plays once, hangs up.
Modes: app (LiveKit room), agent (room + named dispatch), play, record, ignore;
incoming also accepts reject. app/agent require a working LiveKit bridge.
answer=on_join waits for a room participant; auto answers immediately.
The current router treats manual like auto for app/agent; use incoming=ignore
for manual control, then answer/play/bridge explicitly. Read settings.summary
and settings.rules: first matching config rule overrides the direction default.
GET /api/settings reads these; POST /api/settings accepts a partial JSON object.

Manual audio control:
  agent-bluetooth answer
  agent-bluetooth play /abs/greeting.wav
  agent-bluetooth bridge
  agent-bluetooth join operator
play/bridge select the only current call; use --call-id ID if more than one.
play --keep leaves the call up after playback; --loop repeats the WAV.

When a command fails:
  agent-bluetooth debug
  # phone.radio_error {error, hint} set? the dongle isn't working: relay the hint, don't retry
  agent-bluetooth logs -n 100
Check phone.connected, active calls, events and logs before retrying. An accepted
call request is not proof of an answer; call.active is the connection event.

Fake adapters only:
  agent-bluetooth sim sms sender=+15551234567 text=Hello
  agent-bluetooth sim call number=+15551234567
  agent-bluetooth sim remote_hangup
""".strip()
