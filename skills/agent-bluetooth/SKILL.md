---
name: agent-bluetooth
description: Use when the user asks to call, text, check calls or contacts through their phone using the agent-bluetooth daemon.
---

Use the CLI as a client of the running daemon. Global `--host` and `--key`
options go before the command, or use `AGENT_BLUETOOTH_HOST` and
`AGENT_BLUETOOTH_KEY`. Run `agent-bluetooth guide` for API and mode details.
Commands return JSON, except guide, help and logs. Check exit codes.

Start with `agent-bluetooth status` and `agent-bluetooth settings`.
Read `summary` and `rules` before changing call behavior. Settings persist and
apply to subsequent calls. Config rules override direction defaults.

## Send and read SMS

```bash
agent-bluetooth sms +15551234567 "On my way"
agent-bluetooth sms list -n 20
```

The list contains newest-first `{type, data, ts}` events. Incoming messages use
`data.sender` and `data.text`; sent messages use `data.to` and `data.text`.
It retains at most 1000 messages observed since daemon startup, not the phone's
historical inbox. MAP permissions and platform support affect SMS availability;
received text may be truncated unless the daemon enables `fetch_full`.

## Call and wait for an answer

```bash
agent-bluetooth call +15551234567
agent-bluetooth wait call.active --timeout 60 --match call.number=+15551234567
agent-bluetooth dtmf '1#'
agent-bluetooth hangup
```

`call` accepts a request; `call.active` confirms the connection. `wait` watches
live events only and exits 1 on timeout. If the call might answer before `wait`
starts, start the waiter in another terminal first. Use `status` to check a call
already active. Match keys refer to event data; nested keys use dots. Repeat
`--match` to require several fields. `watch call sms` streams JSON lines.

## Find contacts and recent calls

```bash
agent-bluetooth contacts "Anna"
agent-bluetooth history
agent-bluetooth sync
```

Contacts and history require a directory adapter and use its last sync.

## Play a greeting on incoming calls

```bash
agent-bluetooth settings set incoming=play play_file=/abs/greeting.wav
agent-bluetooth settings
```

Use an existing, nonempty 16-bit PCM WAV, mono or stereo, at an absolute path
on the daemon's machine. Incoming play mode answers immediately, plays once,
then hangs up. First matching routing rules still take precedence.

Route by caller (first match wins):

```bash
agent-bluetooth rules add when_contact=false mode=play play_file=/abs/greeting.wav
agent-bluetooth rules add when_contact=true mode=app
agent-bluetooth rules list
```

`status` shows each call's `name` and `contact` (true when the number is in the
phone's contacts).

For manual control, set `incoming=ignore`, then use `answer`, `play /abs/file.wav`
or `bridge`. `answer=manual` currently behaves like auto for app/agent modes;
use ignore to leave calls untouched. To configure room handling:

```bash
agent-bluetooth settings set incoming=agent outgoing=app agent_name=receptionist answer=on_join
```

Room modes need a working LiveKit bridge; `on_join` waits for a participant.

## Diagnose failures

```bash
agent-bluetooth debug
agent-bluetooth logs -n 100
```

First check `phone.radio_error` in `status`: if set, the Bluetooth dongle isn't
working and `hint` says exactly what to do (wrong transport id, missing vendor
firmware, another driver holding the device). Tell the user that step instead of
retrying. Then read phone connection state, current calls, events and logs.
API/request failures exit 1; invalid CLI arguments exit 2. Never retry a call or
SMS blindly after a timeout: inspect status and recent SMS first.
