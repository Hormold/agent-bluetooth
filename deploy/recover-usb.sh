#!/bin/bash
# systemd ExecStartPre: give the daemon a freshly reset dongle on every start.
#
# Cheap dongles survive a dirty daemon exit half-alive; the next open then
# hangs in the kernel (process stuck in D state). Nothing holds the device at
# this point, so power-cycling its USB host controller is safe and resets it.
#
#   VID   USB vendor id of the dongle (default: from transport in config.toml)
#   CTRL  platform name of its host controller (auto-detected when present)
CONFIG=${CONFIG:-$(dirname "$0")/config.toml}
VID=${VID:-$(grep -oE 'usb:[0-9a-fA-F]{4}' "$CONFIG" | head -1 | cut -d: -f2)}
VID=${VID:-33fa}
DRIVER=/sys/bus/platform/drivers/ohci-platform

# 1) reap leftover daemon processes (unclean exit / SIGKILL survivors)
pkill -9 -f "agent-bluetooth serve" || true
for i in $(seq 1 10); do pgrep -f "agent-bluetooth serve" >/dev/null || break; sleep 1; done

# 2) find the dongle's host controller (e.g. 5311400.usb)
if [ -z "$CTRL" ]; then
  for d in /sys/bus/usb/devices/*; do
    [ "$(cat "$d/idVendor" 2>&1)" = "$VID" ] || continue
    CTRL=$(basename "$(readlink -f "$d/../..")")
    break
  done
fi
[ -n "$CTRL" ] || CTRL=$(cat /root/.agent-bluetooth/usb-controller 2>&1 | grep -E '^[0-9a-f]+\.usb$')
[ -n "$CTRL" ] && echo "$CTRL" > /root/.agent-bluetooth/usb-controller

# 3) always power-cycle it, then wait for the dongle to enumerate
if [ -n "$CTRL" ]; then
  echo "recover: power-cycling $CTRL"
  echo "$CTRL" > "$DRIVER/unbind" || true
  sleep 1
  echo "$CTRL" > "$DRIVER/bind" || true
fi
for i in $(seq 1 20); do lsusb | grep -qi "$VID:" && break; sleep 1; done
lsusb | grep -qi "$VID:" && echo "recover: dongle present" || echo "recover: dongle STILL missing"
exit 0
