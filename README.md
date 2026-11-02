# Agent Bluetooth

![Agent Bluetooth](docs/launch/banner.png)

**Turn any phone into an API — calls and SMS — over plain Bluetooth.**

Plug a $15 USB Bluetooth dongle into your Mac or a cheap always-on box
(Raspberry Pi, Orange Pi, any Linux machine) and pair any phone that can use a
Bluetooth headset, as if it were a car kit. Your real number, with
no Twilio, no SIP, no second line, becomes programmable: incoming calls open a
LiveKit room you (or an AI agent) can join and talk, SMS and 2FA codes forward
anywhere, and everything is scriptable from one CLI.

Leave the phone in a drawer; use its line from your laptop, a server, or an
agent.

> Inspired by an idea from **[Grigory Bakunov (@bobuk)](https://github.com/bobuk)** —
> a phone as a universal Bluetooth-attached controller. This is a small, hackable
> build of it. Weekend project; expect rough edges.

## What you can do

- **Answer your phone from anywhere** — each call becomes a LiveKit room; join
  from a laptop (even abroad, no roaming) or hand it to an AI voice agent.
- **Let an agent make and take calls on your number** — dispatch a LiveKit agent
  into the room; it gets the caller's number in metadata.
- **Get your SMS & OTP codes where you are** — forward a code to Telegram, an SMS
  to a webhook, auto-reply when you can't pick up.
- **Record or play audio** — voicemail greetings, IVR prompts, call recording.
- **Rules, no code** — "on event X, do Y" automations (block spam, OTP relay,
  driving auto-reply).

Same options apply to incoming and outgoing calls: answer in an app, send to an
agent, play a recording, or record.

## How it works

```
   your phone ──Bluetooth(HFP/MAP/PBAP)──► box (this software) ──► LiveKit room
                                                │                      ▲
                                        events + HTTP API        you / an agent
```

Pure-Python ([Bumble](https://github.com/google/bumble) — no BlueZ needed). Four
swappable layers behind ports — `phone`, `messages`, `bridge`, `sink` — wired by
one `config.toml`. A native macOS softphone (`apps/AgentPhone`) and a web demo
ship with it; or drive everything from the `agent-bluetooth` CLI.

## Quick start (no hardware)

```bash
uv sync
livekit-server --dev                            # terminal 1
uv run agent-bluetooth serve config.demo.toml   # terminal 2
open http://localhost:8080                       # press "call" under Simulate
```

Real phone: copy `config.example.toml` → `config.toml`, set your Bluetooth
transport + LiveKit creds, pair the phone with **"Agent Bluetooth"**. See
[docs/SETUP.md](docs/SETUP.md). Agents: run `agent-bluetooth guide`.

## Recommended hardware

- **Bluetooth dongle:** [TP-Link UB400](https://www.amazon.com/dp/B07V1SZCY6). Current units are a
  Realtek RTL8761BU (USB id `2357:0604`): transport `usb:2357:0604+sco=2`, plus the
  `rtl8761bu_fw.bin` / `rtl8761bu_config.bin` firmware in `/lib/firmware/rtl_bt/`
  (from linux-firmware; Bumble uploads it on every start). Avoid no-name "CSR8510"
  clones (`33fa:0001`): they wedge on call audio.
- **Host:** your **Mac** works directly (no Linux box needed), or any Linux SBC with
  4 cores for an always-on setup (Raspberry Pi 4/5, Orange Pi 3 LTS tested) on
  Ethernet or 5 GHz Wi-Fi. Always use the USB dongle, not built-in Bluetooth:
  the software drives the dongle directly, and built-in chips (Mac or Pi) don't
  hand call audio to it.
- **Phone:** any phone that can connect to a Bluetooth headset or car kit
  (hands-free profile). Android, iPhone, even a basic feature phone.

Setup details: [docs/SETUP.md](docs/SETUP.md) · systemd + auto-recovery:
[docs/systemd.md](docs/systemd.md) · problems: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Hardware note

Use a **genuine CSR8510 USB dongle** (e.g. TP-Link UB400). Counterfeit CSR
clones enumerate but drop the call link within minutes — avoid them. On
Raspberry/Orange Pi the onboard Bluetooth can't carry call audio (SCO over PCM,
not USB), so the USB dongle is required for voice.

## Status

Verified on real hardware: pairing, SMS (MAP), contacts/history (PBAP), incoming
and outgoing calls, and call audio published into a LiveKit room. It's a
starting point to build on — every capability is an adapter you can swap.
See [docs/DESIGN.md](docs/DESIGN.md) for the roadmap.
