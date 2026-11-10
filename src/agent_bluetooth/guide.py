"""Practical recipes printed by agent-bluetooth guide."""

from __future__ import annotations

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
Forward one-time codes from incoming SMS (Android; iMessage/RCS never arrive):
  agent-bluetooth otp on                       # to the config's telegram sink
  agent-bluetooth otp on --sms +15551234567    # or text them to a number
  agent-bluetooth otp on --webhook https://...  # or POST {text, vars}
  agent-bluetooth otp off | otp
otp installs a runtime automation (settings.automations, persisted) that
extracts the first 4-8 digit run from every sms.received and forwards it.
GET /api/sms?limit=20 returns newest-first sms.received/sms.sent event objects
{type, data, ts}. Received data has sender/text; sent data has to/text.
Only observed events since startup are available, capped at 1000 messages.
This does not fetch old messages from the phone. MAP may truncate received text
unless fetch_full is enabled; phone permissions/platform may limit SMS support.

Call, wait for the other side to answer, press a key, hang up:
  agent-bluetooth call +15551234567
  agent-bluetooth wait call.active --timeout 60 --match call.number=+15551234567

Let a LiveKit voice agent handle the call (dispatched with the task):
  agent-bluetooth call +15551234567 --agent phone-agent --task "Ask if they're open Sunday"
  agent-bluetooth dtmf '1#'
  agent-bluetooth say "Make it 8 instead, and ask for a table outside"
  agent-bluetooth wait call.summary --timeout 900
  agent-bluetooth hangup
say hands the agent on the call a line to speak next (a data message on topic
"owner" in the call room); it needs a call in a room (app/agent mode).
call.summary is posted by the agent when the call ends: data.outcome (one or
two sentences), data.details (last lines), data.transcript (file path),
data.recording (when record_calls is on). Sinks (telegram, webhook) and
automations receive it like any event. POST /api/calls/summary {call_id|room,
outcome, details?, transcript?} is how an agent reports it.
An agent that needs the owner mid-call raises Agent Phone's alert card:
POST /api/calls/attention {kind: person|question|clear, text, room|call_id}
-> a call.attention event; the app jumps to the front with a sound. The
owner's typed answer comes back as a say line (topic "owner"). Try it:
  agent-bluetooth attention question "What ZIP code is on the account?"
  agent-bluetooth attention clear
Phone menus and hold queues: examples/livekit-agent/ivr_agent.py, and the
phone-menu-calls skill. Owner API for agent authors: docs/OWNER.md.
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
Modes: app (LiveKit room), agent (room + named dispatch), play, stall, ignore;
incoming also accepts reject. app/agent require a working LiveKit bridge.
answer=on_join waits for a room participant; auto answers immediately.
The current router treats manual like auto for app/agent; use incoming=ignore
for manual control, then answer/play/bridge explicitly. Read settings.summary
and settings.rules: first matching config rule overrides the direction default.
GET /api/settings reads these; POST /api/settings accepts a partial JSON object.

Route by caller (first match wins; settings.rules, persisted):
  agent-bluetooth rules add when_contact=false mode=play play_file=/abs/greeting.wav
  agent-bluetooth rules add when_contact=true mode=agent agent_name=receptionist
  agent-bluetooth rules add when_number='+1800*' mode=reject
  agent-bluetooth rules list | rules remove 0 | rules clear
  agent-bluetooth rules preset contacts-only|screen-unknown|block-tollfree
  agent-bluetooth block <number...> [--stall] [--file list.txt] | unblock | blocked
mode=stall answers and plays random phrases from stall_dir between the caller's
replies for stall_minutes (default 5), then hangs up (wastes spammers' time).
Conditions: when_contact (from synced contacts), when_number (glob), direction
in|out|any. Per-rule agent_name / play_file override the defaults. status shows
each call's name and contact flag.

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
A dial the phone refuses arrives as call.failed (same request_id as the call reply).
answer/hangup/dtmf/volume exit 1 when the phone refused (502) or didn't reply (504).

Fake adapters only:
  agent-bluetooth sim sms sender=+15551234567 text=Hello
  agent-bluetooth sim call number=+15551234567
  agent-bluetooth sim remote_hangup
""".strip()
