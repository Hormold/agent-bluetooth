# Agent Bluetooth

Turn any phone into an API and a LiveKit room over Bluetooth. Pair a small
device (Raspberry Pi, any Linux/macOS box with a Bluetooth dongle) to your phone
as a hands-free kit, and you get:

- **Incoming calls** → a LiveKit room (you or an AI agent join and talk), or a
  played WAV, or a rule-based decision — all configurable.
- **SMS** in and out → events you can forward anywhere (Telegram, webhook, …).
- **One CLI** an agent drives like `agent-browser`: `watch`, `call`, `answer`,
  `play`, `bridge`, `sms`, `sim`.

No Twilio, no SIP trunk — the call runs on your real phone and its real number.
It's a demo / starting point, not a product: everything is an adapter you swap.

## Quick start (no hardware)

```bash
uv sync
livekit-server --dev            # terminal 1
uv run agent-bluetooth serve config.demo.toml   # terminal 2
open http://localhost:8080      # press "call" under Simulate
```

Drive it from a third terminal:

```bash
uv run agent-bluetooth watch              # live JSON event stream
uv run agent-bluetooth sim call number=+15551112222 name=Mom
uv run agent-bluetooth play greeting.wav  # answer + play a clip, then hang up
uv run agent-bluetooth bridge             # answer + audio into a LiveKit room
uv run agent-bluetooth join you           # -> {url, token} to join the call
uv run agent-bluetooth guide              # full recipe for agents
```

## Real phone

Get a **USB Bluetooth dongle** (CSR8510 or RTL8761B, ~$10). A Pi/Mac/PC
built-in radio often can't route call audio (SCO) over HCI — the dongle can.
Then:

```bash
cp config.example.toml config.toml     # edit transport + LiveKit creds
uv run agent-bluetooth serve config.toml
# pair your phone with "Agent Bluetooth" in its Bluetooth settings
```

- **Calls**: Hands-Free Profile (HFP). Android & iPhone both expose it.
- **SMS**: Message Access Profile (MAP). Android: allow message access when
  asked. iPhone: enable "Show Notifications" for the device in Bluetooth
  settings. iOS may refuse outbound SMS; inbound works.

## Architecture

Four swappable layers, wired by `config.toml`. Adapters never touch each other —
they only speak `Event`s on a bus and move PCM through an `AudioPipe`.

| Layer | Port | Adapters | Swap to… |
| --- | --- | --- | --- |
| `phone` | calls + call audio | `bumble-hfp`, `fake` | another radio / protocol |
| `messages` | SMS in/out | `bumble-map`, `fake`, `none` | RCS, a modem, an API |
| `bridge` | where audio goes | `livekit` | WebRTC, SIP, a recorder |
| `sink` | where events go | `log`, `webhook`, `telegram` | Slack, a queue, your agent |

Point any layer at your own class: `adapter = "my_pkg.my_mod:MyClass"`.

```
phone adapter ─┐                        ┌─ sinks (telegram / webhook / log)
  (HFP/fake)   ├─ events ─ EventBus ────┤
               │                        └─ CallRouter ─ policy ─┐
  AudioPipe ───┘                                                │
      └──────────────── AudioHandler (LiveKit room | WAV player) ┘
                                   ▲
                         HTTP API + web UI + CLI
```

Built on [Bumble](https://github.com/google/bumble) (pure-Python Bluetooth, no
BlueZ/oFono needed) and [LiveKit](https://livekit.io).

## Status

Verified end-to-end on the fake adapters against `livekit-server --dev`
(incoming call → room, play WAV → auto-hangup, SMS in/out, join token). The
Bumble HFP and MAP adapters are written against Bumble's HFP example and the
MAP/OBEX spec but are **not yet hardware-tested** — needs a dongle + phone.

## Prior art

[Bumble](https://github.com/google/bumble) HFP example · Asterisk `chan_mobile`
· oFono + PipeWire on a Pi ([Sipfront write-up](https://sipfront.com/blog/2025/03/automating-calls-on-real-mobile-devices-with-bluetooth-hfp-on-a-raspberry-pi/))
· `imsg-obex` / `mcbluetooth` for MAP on iPhone · Windows "Phone Link" as the
commercial analogue. This project's angle: pure-Python, adapter-swappable,
agent-first CLI, and every call lands in a LiveKit room.
