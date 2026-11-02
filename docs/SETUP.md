# Setup

You need a USB Bluetooth dongle, a computer to plug it into (a Mac, or any
Linux box), and a phone. The software drives the dongle directly over USB, so
the computer's own Bluetooth stays out of it.

## 1. Install

```bash
git clone https://github.com/Hormold/agent-bluetooth && cd agent-bluetooth
uv tool install .        # puts `agent-bluetooth` on PATH
```

- **macOS:** `brew install libusb livekit`
- **Linux:** `apt install libusb-1.0-0`, plus [livekit-server](https://docs.livekit.io/home/self-hosting/local/).
  If the system Bluetooth service grabs the dongle, stop it or unbind the
  kernel driver for that device.

## 2. Configure

```bash
cp config.example.toml config.toml
```

Set `[bluetooth] transport` to your dongle: `usb:VID:PID+sco=2` (find VID:PID
with `lsusb` on Linux or System Information > USB on macOS; `usb:0+sco=2` takes
the first dongle). `+sco=2` turns on call audio.

For LiveKit, the defaults point at a local dev server (`ws://127.0.0.1:7880`,
`devkey` / `secret`). Use your LiveKit Cloud project instead if you have one.

## 3. Run

```bash
livekit-server --dev --bind 127.0.0.1 --node-ip 127.0.0.1   # terminal 1
agent-bluetooth serve config.toml                     # terminal 2
```

On the phone: Bluetooth settings, pair with **Agent Bluetooth**, allow access to
contacts and messages. Then open the Mac app (`apps/AgentPhone`) or drive it
from the CLI: `agent-bluetooth status`.

If the radio doesn't come up, `agent-bluetooth status` shows
`phone.radio_error` with the reason and what to do (wrong transport id, a
vendor firmware file the dongle needs, another driver holding it).

Tested with a TP-Link UB400 (current units are a Realtek RTL8761BU, which needs
the `rtl8761bu_*` firmware files from linux-firmware) on macOS and an Orange Pi.

## What happens on a call

```bash
agent-bluetooth settings                     # what happens now, in plain words
agent-bluetooth settings set incoming=app    # ring in the app (default)
agent-bluetooth settings set incoming=agent agent_name=phone-receptionist
agent-bluetooth settings set incoming=play play_file=/abs/path/greeting.wav
```

Route by who calls (first match wins; unmatched calls use the mode above):

```bash
agent-bluetooth rules add when_contact=false mode=play play_file=/abs/path/greeting.wav
agent-bluetooth rules add when_contact=true mode=agent agent_name=phone-receptionist
agent-bluetooth rules add when_number='+1800*' mode=reject
agent-bluetooth rules list          # or: rules remove 0 | rules clear
```

Quick setups:

```bash
agent-bluetooth rules preset contacts-only     # decline everyone not in contacts
agent-bluetooth rules preset screen-unknown    # keep unknown numbers busy (below)
agent-bluetooth rules preset block-tollfree    # decline +1 800/888/877/... numbers
agent-bluetooth block +14155550199             # blocklist; --file list.txt to import
agent-bluetooth block +14155550177 --stall     # ...or keep that one busy instead
agent-bluetooth blocked | unblock <number>
```

**Keep them busy** (`mode=stall`): answers, plays a random phrase from a folder
of WAVs ("Hello? Sorry, can you repeat that?"), waits for the caller to speak and
go quiet, plays the next, for up to `stall_minutes` (default 5), then hangs up.
Record the phrases in your own voice: `agent-bluetooth settings set
stall_dir=/abs/path/phrases`.

Conditions: `when_contact` (true/false, from the synced phonebook), `when_number`
(glob), `direction` (in/out/any). A rule can carry its own `agent_name` or
`play_file`.

Modes: `app` (a person joins the call room), `agent` (a LiveKit agent is
dispatched, see [LIVEKIT.md](LIVEKIT.md)), `play` (answer, play a 16-bit WAV,
hang up), `record`, `reject`, `ignore`. The Mac app sets the same thing under
Settings > When someone calls.

## Phones

Calls work with any phone that can use a Bluetooth headset. Android: SMS (send
and receive), contacts and call history, tested. iPhone: calls, contacts and
call history; iOS also shares messages with car kits over Bluetooth (MAP), but
that path isn't tested here yet.

## Always-on box (Linux, systemd)

The daemon speaks `Type=notify` with a watchdog, so systemd restarts it if it
hangs:

```ini
# /etc/systemd/system/agent-bluetooth.service
[Unit]
Description=Agent Bluetooth
After=network.target
StartLimitIntervalSec=0

[Service]
Type=notify
NotifyAccess=all
WatchdogSec=30
TimeoutStopSec=10
KillMode=mixed
WorkingDirectory=/opt/agent-bluetooth
# path from `which agent-bluetooth`
ExecStart=/root/.local/bin/agent-bluetooth serve config.toml
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

`systemctl daemon-reload && systemctl enable --now agent-bluetooth`

`deploy/recover-usb.sh` is an optional `ExecStartPre` for boards whose USB
controller can be reset from sysfs (it power-cycles the dongle's OHCI
controller before each start). It's board-specific: read it before using it.
