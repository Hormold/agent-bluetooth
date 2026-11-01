# Running as a service (Linux)

The daemon tells systemd it's ready (`Type=notify`) and sends a watchdog
heartbeat; liveness is decoupled from the radio, so a flaky dongle never blocks
startup. `ExecStartPre` reaps a stale process and, if the dongle fell off the
bus, power-cycles its USB controller — so restarts recover with no replug.

`/etc/systemd/system/agent-bluetooth.service`:

```ini
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
WorkingDirectory=/root/agent-bluetooth
Environment=PATH=/root/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin
ExecStartPre=/root/agent-bluetooth/recover-usb.sh
ExecStart=/root/.local/bin/uv run agent-bluetooth serve config.toml
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

`recover-usb.sh` (set `CTRL` to your dongle's USB controller, from
`readlink -f /sys/bus/usb/devices/usbN` → the `*.usb` platform device):

```bash
#!/bin/bash
VID=0a12                 # your dongle's USB vendor id
CTRL=5311400.usb         # the dongle's USB controller (platform device)
pkill -9 -f "agent_bluetooth serve" || true
for i in $(seq 1 10); do pgrep -f "agent_bluetooth serve" >/dev/null || break; sleep 1; done
if ! lsusb | grep -qi "$VID"; then
  echo "$CTRL" > /sys/bus/platform/drivers/ohci-platform/unbind || true
  sleep 1
  echo "$CTRL" > /sys/bus/platform/drivers/ohci-platform/bind || true
  for i in $(seq 1 20); do lsusb | grep -qi "$VID" && break; sleep 1; done
fi
```

```bash
systemctl daemon-reload && systemctl enable --now agent-bluetooth
```

> Most SBC USB-A ports have no software VBUS control, so a dongle that wedges
> *electrically* still needs a physical replug. A genuine CSR8510 rarely wedges;
> for full remote power control, put the dongle on a uhubctl-capable hub.
