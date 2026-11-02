# Setup (real phone)

1. **Dongle.** Plug a genuine CSR8510 USB Bluetooth dongle into the box.
   Find its id: `lsusb` (e.g. `0a12:0001`).

2. **Deps.** `uv sync`. On Linux also: `apt install libusb-1.0-0`, and
   `systemctl stop bluetooth` so it doesn't hold the dongle (we use libusb).

3. **Config.** `cp config.example.toml config.toml` and set:
   - `[bluetooth] transport` — `usb:<VID>:<PID>+sco=2` (the `+sco=2` enables call
     audio; `usb:0` also works if it's the only dongle).
   - `[bridge]` — your LiveKit `url` / `api_key` / `api_secret` (LiveKit Cloud,
     or `livekit-server --dev` on the box).

4. **Run.** `uv run agent-bluetooth serve config.toml`.
   On your phone: Bluetooth → pair with **"Agent Bluetooth"**.

5. **Keep it running** (Linux): a systemd unit with `Type=notify`,
   `WatchdogSec=30`, `Restart=always`, and an `ExecStartPre` that reaps stale
   processes / power-cycles the USB controller. Example in
   [docs/systemd.md](systemd.md).

## Choosing the call behaviour

Set it once in `[routing]` (or live over `/api/settings`, or in the app):

| mode | what happens on a call |
| --- | --- |
| `app` | audio → LiveKit room; a human joins to talk |
| `agent` | audio → room + dispatch a LiveKit agent (`agent_name`), caller number in metadata |
| `play` | play a WAV into the line |
| `record` | record the call to a WAV |
| `reject` | (incoming) hang up |

## Running on a Mac (no Linux box)

The dongle is driven straight over USB, so macOS works the same as Linux:

```bash
brew install libusb livekit
mkdir -p ~/.agent-bluetooth/rtl_bt   # Realtek firmware (TP-Link UB400 = RTL8761BU)
curl -fsSLo ~/.agent-bluetooth/rtl_bt/rtl8761bu_fw.bin \
  https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtl_bt/rtl8761bu_fw.bin
curl -fsSLo ~/.agent-bluetooth/rtl_bt/rtl8761bu_config.bin \
  https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtl_bt/rtl8761bu_config.bin

livekit-server --dev --bind 127.0.0.1 --node-ip 127.0.0.1      # terminal 1
BUMBLE_RTK_FIRMWARE_DIR=~/.agent-bluetooth/rtl_bt \
  uv run agent-bluetooth serve config.toml                       # terminal 2
```

In `config.toml`: `transport = "usb:2357:0604+sco=2"`, LiveKit `url` and
`client_url` = `ws://127.0.0.1:7880`. macOS leaves third-party dongles alone, so
nothing needs to be disabled. Moving a dongle between machines keeps its
Bluetooth address: copy `~/.agent-bluetooth/keys.json` along and the phone
reconnects without pairing again.

## Platform notes

- **Android**: SMS send/receive (MAP), contacts + call history (PBAP) all work.
- **iPhone**: Apple blocks SMS over Bluetooth; use notifications instead (ANCS —
  on the roadmap). Calls, contacts, call history work.
- **Any phone with Bluetooth headset support** works for calls (hands-free profile).
- **Built-in Bluetooth** (Mac or Pi) can't be used — plug in the USB dongle.
