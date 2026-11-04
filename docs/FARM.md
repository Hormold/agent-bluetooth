# Many phones: one daemon per dongle

One daemon drives one Bluetooth dongle and one phone. For more phones, plug in
more dongles and run one daemon per dongle. They share nothing: a dongle that
wedges restarts its own daemon while the others keep their calls.

## 1. Find the dongles

```bash
agent-bluetooth radios
```

Each entry has a `transport` like `usb:1-1.2+sco=2`: bus 1, port 1.2. Use that
form. Cheap clones often share VID:PID and serial number, and `usb:0` indexes
shift on replug; the port path stays put as long as the dongle stays in that
port. (Linux built-in adapters: `hci-socket:N`.)

## 2. Describe the instances

Any config becomes a farm by adding `[[instances]]`. Each instance is the
shared config with its own table merged on top:

```toml
[phone]
adapter = "bumble-hfp"
[bridge]
adapter = "livekit"
[http]
port = 8080                  # instance i gets 8080 + i unless it sets its own

[[instances]]
name = "pixel"
transport = "usb:1-1.2+sco=2"

[[instances]]
name = "iphone"
transport = "usb:1-1.3+sco=2"
routing = { incoming = "agent", agent_name = "receptionist" }
```

Per instance, by default:

| | default |
|---|---|
| state (keys, settings, recordings, pid) | `~/.agent-bluetooth/instances/<name>/` (`state_dir = ...` to move it) |
| HTTP port | base port + position in the list |
| name the phone sees when pairing | `Agent Bluetooth <name>` |
| LiveKit room names | `call-<name>-...` |
| events, webhooks | carry `"instance": "<name>"` |

`agent-bluetooth instances` prints the resolved table and refuses configs where
two instances share a port, transport or keystore.

A config without `[[instances]]` is one instance named `default` with state in
`~/.agent-bluetooth`, as before.

## 3. Run them

Any host:

```bash
agent-bluetooth farm config.toml            # all; or: farm config.toml pixel
```

One process per instance, output prefixed with `[name]`. An instance that exits
is restarted with backoff (1 s doubling to 30 s, reset after a minute up); the
others aren't touched. Ctrl-C stops all.

Linux with systemd (adds the hang watchdog): copy
`deploy/agent-bluetooth@.service` and enable one unit per instance:

```bash
systemctl enable --now agent-bluetooth@pixel agent-bluetooth@iphone
```

## 4. Talk to one

```bash
agent-bluetooth -c config.toml -i pixel status
AGENT_BLUETOOTH_INSTANCE=iphone agent-bluetooth sms +1555... "hi"
```

`-i` takes the port and API key from the config.

## Limits

- One phone per dongle. Bluetooth would allow several phones on one
  controller, but cheap dongles carry one call-audio (SCO) link reliably, and
  one dongle per phone keeps faults apart. A dongle costs less than the debugging.
- USB bandwidth: every call is an isochronous stream. Spread dongles over root
  hubs/controllers (`lsusb -t`) rather than one cheap hub. How many concurrent
  calls one controller carries cleanly is not measured yet; crackle is the symptom.
- `deploy/recover-usb.sh` power-cycles a whole USB controller. With
  `INSTANCE` set, it only does that when no sibling daemon is running.
