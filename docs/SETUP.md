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

## Platform notes

- **Android**: SMS send/receive (MAP), contacts + call history (PBAP) all work.
- **iPhone**: Apple blocks SMS over Bluetooth; use notifications instead (ANCS —
  on the roadmap). Calls, contacts, call history work.
- **Onboard Pi Bluetooth** can't carry call audio — use the USB dongle.
