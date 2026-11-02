<img src="docs/assets/banner.png" alt="Agent Bluetooth" width="100%">

**Your phone, as an API for agents.** A $15 USB Bluetooth dongle pairs with your
phone like a headset. Calls, texts and contacts become a CLI, an HTTP API and
LiveKit rooms. No Twilio, no SIP trunk, no new number: your real one.

<p>
  <img src="docs/assets/terminal.png" alt="Claude texting through agent-bluetooth" width="62%">
  <img src="docs/assets/app.png" alt="Agent Phone for macOS" width="34%">
</p>

- **Calls.** Dial and answer from code. Each call is a LiveKit room where the
  phone shows up as a SIP participant with native DTMF, so LiveKit telephony
  agents work unchanged.
- **Texts** (Android): send, list, wait for a reply.
- **Contacts and call history**, synced from the phone.
- **Agent Phone**, a small macOS softphone
  ([download](https://github.com/Hormold/agent-bluetooth/releases/latest/download/Agent-Phone-0.1.0.zip)).
  **A Claude Code skill**, so your agent can text and call.

## You need

- A USB Bluetooth dongle. Tested: [TP-Link UB400](https://www.amazon.com/dp/B07V1SZCY6).
- A Mac or any Linux machine to plug it into.
- Any phone that can connect to a Bluetooth headset.

## Run it

```bash
brew install libusb livekit            # Linux: libusb-1.0-0 + livekit-server
git clone https://github.com/Hormold/agent-bluetooth && cd agent-bluetooth
uv tool install . && cp config.example.toml config.toml   # puts `agent-bluetooth` on PATH

livekit-server --dev --bind 127.0.0.1 --node-ip 127.0.0.1 &
agent-bluetooth serve config.toml
```

Pair your phone with **Agent Bluetooth**. If the dongle doesn't come up,
`agent-bluetooth status` says why and what to do. More (Linux, systemd,
call modes): [docs/SETUP.md](docs/SETUP.md).

## From an agent

```bash
agent-bluetooth sms +14155550142 "Running 10 min late"
agent-bluetooth wait sms.received --match sender=+14155550142
agent-bluetooth call +14155550142 && agent-bluetooth wait call.active
agent-bluetooth dtmf 1
agent-bluetooth contacts anna
agent-bluetooth debug                  # when something's off
```

Every command prints JSON. For Claude Code, copy the skill:
`cp -r skills/agent-bluetooth ~/.claude/skills/`. Then just ask it to text or
call someone. Full recipe: `agent-bluetooth guide`.

## A voice agent on your calls

[examples/livekit-agent](examples/livekit-agent) is a small receptionist on
LiveKit Agents (greets the caller, can press keys, hangs up):

```bash
cd examples/livekit-agent && uv sync
uv run python -m livekit.agents start agent.py --dev
agent-bluetooth settings set incoming=agent agent_name=phone-receptionist
```

How the phone appears in the room: [docs/LIVEKIT.md](docs/LIVEKIT.md).

## Mac app

<p>
  <img src="docs/assets/app.png" alt="Recents" width="32%">
  <img src="docs/assets/app-setup.png" alt="Setup checklist" width="32%">
  <img src="docs/assets/app-settings.png" alt="Call settings" width="32%">
</p>

**[Download Agent Phone for macOS](https://github.com/Hormold/agent-bluetooth/releases/latest)**
(signed and notarized, macOS 14+). Unzip, drag it to Applications, open.

Or build it yourself:

```bash
apps/AgentPhone/install.sh       # builds it and installs "Agent Phone" into /Applications
```

On first launch it checks everything it needs (the service, the dongle, the
phone, LiveKit, microphone, notifications) and sets up what it can with one
button: installs and starts the local service and LiveKit through Homebrew and
uv, or connects LiveKit Cloud if you want hosted voice agents. Then it's a
phone: recents, contacts, keypad, incoming call alerts, and a choice of what
happens when someone calls.

## Docs

[Setup](docs/SETUP.md) · [LiveKit](docs/LIVEKIT.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)

Inspired by an idea from [Grigory Bakunov (@bobuk)](https://github.com/bobuk).
