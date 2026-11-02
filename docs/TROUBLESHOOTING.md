# Troubleshooting

Known problems, how to recognise them, and the fix. Add a row whenever you hit
something new. Logs: `journalctl -u agent-bluetooth -f` (run without `-v`).

## Connection

| Symptom (log) | Cause | Fix |
| --- | --- | --- |
| Phone never reconnects; log shows `Connection Authentication …` then `reconnect … timed out` | Reconnect timeout too short — on a slow dongle connect + auth + encryption already eats it, so the HFP handshake is killed mid-way | Fixed: paging gets 12 s, the HFP setup after linking gets its own 25 s |
| Pressing **Connect** on the phone does nothing | Box keeps paging the phone; while paging the controller can't page-scan, so it can't accept the phone's own connect | Fixed: long connectable windows (`reconnect_every`, 25 s), short outbound pages |
| `reconnect failed: CONNECTION_ALREADY_EXISTS` | A half-open ACL link is still there | Fixed: reuse the existing ACL instead of connecting again |
| App shows an active call that ended long ago | A `+CIEV call=0` was dropped by the controller | Fixed: call state comes from indicators + `AT+CIND?` heartbeat; 3 missed heartbeats → link is recycled |
| Link "connected" but nothing responds | Zombie HFP link (ACL up, no AT replies) | Fixed: heartbeat recycles it automatically |

## USB dongle

| Symptom (log) | Cause | Fix |
| --- | --- | --- |
| New Realtek dongle (`2357:0604`): radio never comes up / `rtk` firmware errors | RTL8761BU needs firmware uploaded on every start | Put `rtl8761bu_fw.bin` + `rtl8761bu_config.bin` from linux-firmware into `/lib/firmware/rtl_bt/` |
| After swapping dongles the phone won't reconnect | New dongle = new Bluetooth address; the phone's bond is with the old one | Forget "Agent Bluetooth" on the phone and pair again |
| After replugging the dongle nothing works; `LIBUSB_ERROR_NO_DEVICE` spam | Daemon still holds the old USB handle | Fixed: a watcher sees the device vanish/re-enumerate and exits → systemd restarts with a fresh handle |
| `radio open … LIBUSB_ERROR_BUSY` forever | Kernel `btusb` grabbed the dongle, or a failed open left the interface claimed | `echo "blacklist btusb" > /etc/modprobe.d/agent-bluetooth-no-btusb.conf && modprobe -r btusb` (onboard Pi BT uses `hci_uart`, unaffected). Code releases the claim on failure and exits after 3 failed rounds |
| Service stuck `activating`, `address already in use` on 8080, process won't die | Power-cycling the USB controller while the daemon held the device left a D-state process | Never rebind the controller from inside the daemon; `recover-usb.sh` (ExecStartPre) reaps stale processes first |
| `device descriptor read/64, error -110`, `unable to enumerate USB device` | Counterfeit CSR clone wedged electrically | Physical replug (SBC USB-A ports have no software power control) or put it on a uhubctl-capable hub. Use a **genuine CSR8510** (TP-Link UB400) |
| `hci-socket … not supported on this platform` | uv's standalone Python has no `AF_BLUETOOTH` | Use the USB transport: `usb:<VID>:<PID>+sco=2` |

## Call audio

| Symptom (log) | Cause | Fix |
| --- | --- | --- |
| Recording is 0 bytes, `sco_interface=<none>` | SCO over USB is off by default in Bumble | Add `+sco=2` to the transport (`usb:33fa:0001+sco=2`) |
| `LIBUSB_ERROR_INVALID_PARAM` with `+sco=0` | Auto-pick chooses alt 5, which this OHCI controller rejects | Use explicit `+sco=2` (CVSD, 16-bit, one link) |
| Choppy / stuttering audio | SCO frames dropped on a full-speed bus under load | Expected on cheap clones; LiveKit's jitter buffer smooths it. Genuine dongle helps |
| Onboard Raspberry/Orange Pi Bluetooth: calls connect, no audio | Onboard BT routes SCO over PCM, not HCI | Use a USB dongle |
| During a call AT commands (hang up, heartbeat) and HCI commands time out; call can't be hung up, link gets recycled; audio crackles | Bumble's USB sink sends commands, ACL and SCO through one queue with one transfer in flight, so audio blocks everything; a single ISO OUT transfer also starves audio | `usb_sink.py` gives SCO its own lane (pool of 4 ISO transfers, bounded queue). **Not enough on the 33fa:0001 clone over OHCI**: with SCO up the controller stops answering and the radio link dies (`reason=8`, supervision timeout). Fix = different hardware path: genuine dongle, or the dongle behind a high-speed USB hub |
| Reconnect loop silently stops after a link collision (`command result mismatch`) | `set_connectable()` had no timeout and hung on a lost HCI response | Every HCI call in the loop is time-boxed; 2 HCI timeouts or 90 s without the phone after a lost link → clean process restart |
| Process stuck in `D` state, `unbind` and `reboot -f` hang, then `device not accepting address, error -62` | Device closed with ISO transfers still in flight (killed during shutdown) → kernel USB wedge | Always wait for cancelled ISO transfers before closing (`ScoLaneSink.terminate`). Recovery: `echo b > /proc/sysrq-trigger`, then **physical replug** (warm reboot doesn't cut VBUS on this board) |
| Shrinking `MAX_SCO_IN_PACKETS` (4×16 ms) | ACL stalls even with no call on this OHCI port | Keep Bumble's default ISO IN batches |

| Every restart: `shutdown timed out` + libusb assertion, next start stuck in `D` state on `set_interface_alt_setting` (`LIBUSB_ERROR_OTHER`) | Dirty exit leaves the clone half-alive | `deploy/recover-usb.sh` (ExecStartPre) now **always** power-cycles the dongle's USB controller before start |

## Contacts / history

| Symptom | Cause | Fix |
| --- | --- | --- |
| Contacts take minutes or never arrive, history is fast | PBAP filter `0` = all vCard fields, including contact photos | Request only VERSION, FN, N, TEL (+ call datetime for history) |
| App lists empty after relaunch until the phone answers | Directory was fetched live from the phone | Box keeps the last sync on disk (`~/.agent-bluetooth/directory.json`, 0600) and serves it instantly; app keeps a local copy too; sync on connect, after calls, or Debug → Sync now |

## LiveKit / app

| Symptom | Cause | Fix |
| --- | --- | --- |
| Bluetooth recording sounds fine, the same call in LiveKit tears and crackles | Uplink fed LiveKit 8 kHz audio in irregular ~3 ms chunks from USB bursts; only ~30% arrived | Uplink drains each burst, resamples to 48 kHz and captures strict 10 ms frames (LiveKit's documented pattern), with a 120 ms prebuffer. Check: call stats panel, Bluetooth "Delivered" ~100%, LiveKit "Concealed" ~0% |
| LiveKit audio plays some chunks twice and skips others (worse on fast hosts like a Mac) | Audio-up is announced on dialing and again on active; both attach calls passed the "already attached" check while the room was connecting, publishing two phone tracks with two uplinks splitting one pipe | Attach is serialized per room and re-checked under the lock; a re-attach unpublishes the old track. Check: probe sees exactly 1 phone track |
| Call drops mid-conversation, log `HFP link unresponsive (3 missed heartbeats); recycling` | AT replies get slow under load during a call; the heartbeat killed a working link | Heartbeat misses don't count while call audio is arriving |
| "Room not ready" in the app | Call routed in `record`/`play` mode: no room exists by design | Expected; switch the mode to `app` to join from the app |
| Testing audio quality | Talking clocks (e.g. 202-762-1401) sound choppy by design; some IVRs hang up after seconds | Use a line with continuous speech, e.g. an NWS recorded forecast (716-565-0802) |
| Measuring with a probe on the box itself skews results | A second LiveKit client on the SBC steals CPU from the uplink | Probe from another machine |
| App: `Transport … state changed to .failed`, no sound | (1) macOS sandbox without `network.server` → WebRTC can't bind UDP, only TCP-active candidates; (2) LiveKit advertising its public IP | (1) `com.apple.security.network.server` entitlement; (2) `livekit-server --node-ip <box LAN IP>` |
| App shows `500 Server got itself in trouble` when calling | Phone not connected; adapter raised RuntimeError | API now returns `503 phone not connected` |
| App: `Join failed: room not ready` | App read the room from `phone.call` (no room) instead of `calls[]` | Fixed |
| Web demo: `Cannot read properties of undefined (reading 'getUserMedia')` | Browsers expose the mic only on https or localhost | Use the native app, or serve the web UI over https |
| Contacts empty while call history loads | Phones allow one PBAP session at a time | Fixed: PBAP pulls are serialized |
| Contacts/history `503 phone not connected` | They're read from the phone over Bluetooth | Connect the phone first |

## Performance

| Symptom | Cause | Fix |
| --- | --- | --- |
| Load average 7+, systemd/journald 80% CPU, link drops | DEBUG logging of every SCO packet | Don't run with `-v` in production; Bumble's per-packet loggers are capped at INFO |
