# Troubleshooting

Start with `agent-bluetooth debug` (phone, radio, calls, recent events, log
tail) and `agent-bluetooth logs -n 200`. If `phone.radio_error` is set, its
`hint` names the fix.

## Dongle

| Symptom | Fix |
| --- | --- |
| `radio_error`: no dongle matched the transport | Check `lsusb` / System Information for the VID:PID and set `transport = "usb:VID:PID+sco=2"`. |
| `radio_error`: firmware file not found | The dongle needs a vendor firmware file. Realtek: get the named file from linux-firmware (`rtl_bt/`) into the directory set by `BUMBLE_RTK_FIRMWARE_DIR`. |
| `LIBUSB_ERROR_BUSY` | Another driver holds the dongle. Linux: stop `bluetoothd` or unbind `btusb` for that device. |
| Calls connect but there's no audio | Add `+sco=2` to the transport. Built-in Bluetooth (Mac or Pi) can't carry call audio to the software; use a USB dongle. |
| Dongle stops responding after a while, needs replugging | Cheap no-name "CSR8510" clones (`33fa:0001`) do this under call audio. Use a known-good dongle. |

## Phone

| Symptom | Fix |
| --- | --- |
| Swapped dongles, phone won't connect | A new dongle has a new Bluetooth address. Forget "Agent Bluetooth" on the phone and pair again. (Moving the same dongle to another computer: copy `~/.agent-bluetooth/keys.json` along.) |
| Contacts / history: `phone not connected` | They're read from the phone over Bluetooth; connect it first. After that they're served from the last sync. |
| No SMS on iPhone | Apple doesn't expose SMS over Bluetooth. Calls, contacts and history work. |

## Calls and LiveKit

| Symptom | Fix |
| --- | --- |
| App: "Room not ready" | The call is in `play` / `record` mode, which opens no room. Switch to `app`. |
| App can't connect to the room (`Transport ... failed`) | LiveKit advertises an address the app can't reach. Run `livekit-server --node-ip <reachable IP>`; locally `--node-ip 127.0.0.1`. |
| Daemon uses an old LiveKit server after moving machines | Settings saved from the app (`~/.agent-bluetooth/settings.json`) override `config.toml`. Fix it in the app (Settings > Advanced) or `agent-bluetooth settings set livekit.url=ws://127.0.0.1:7880`. |
| Audio quality check | Use a line with continuous speech (e.g. an NWS recorded forecast, +1 716-565-0802). Talking clocks sound choppy by design. |
