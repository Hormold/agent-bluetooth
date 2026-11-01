"""Software "replug" for a wedged USB radio — no hands required.

Cheap dongles lock up (unclean exit, a crash mid-SCO, a flaky bus) and can even
fall off the USB bus so the kernel can't re-enumerate them. On an SBC there's no
one to pull the plug, so we power-cycle the dongle's USB *controller* in
software by unbinding and rebinding its platform driver. That re-enumerates only
that controller's bus, leaving other USB devices (e.g. an SDR on another
controller) untouched.

Linux/sysfs only; a no-op elsewhere or when the controller can't be resolved.
"""

from __future__ import annotations

import glob
import logging
import os
import time

log = logging.getLogger(__name__)
_SYS_USB = "/sys/bus/usb/devices"
_PLATFORM_DRIVERS = "/sys/bus/platform/drivers"


def parse_usb_ids(spec: str) -> tuple[int, int] | None:
    """VID:PID out of a Bumble transport spec like 'usb:33fa:0001+sco=2'."""
    if not spec.startswith("usb:"):
        return None
    body = spec[4:].split("+", 1)[0].rstrip("!")
    if ":" not in body:
        return None
    try:
        vid, pid = body.split(":")
        return int(vid, 16), int(pid, 16)
    except ValueError:
        return None


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def controller_for_device(vendor_id: int, product_id: int) -> tuple[str, str] | None:
    """Find (driver_dir, controller_name) of the USB controller a present
    VID:PID device sits on, by walking its sysfs path up to the platform HCD."""
    for sysdir in glob.glob(f"{_SYS_USB}/*"):
        if _read(f"{sysdir}/idVendor") != f"{vendor_id:04x}":
            continue
        if _read(f"{sysdir}/idProduct") != f"{product_id:04x}":
            continue
        real = os.path.realpath(sysdir)
        # e.g. /sys/devices/platform/soc/5311400.usb/usb6/6-1  -> "5311400.usb"
        for part in real.split("/"):
            if part.endswith(".usb"):
                controller = part
                for drv in ("ohci-platform", "ehci-platform"):
                    if os.path.exists(f"{_PLATFORM_DRIVERS}/{drv}/{controller}"):
                        return f"{_PLATFORM_DRIVERS}/{drv}", controller
    return None


def rebind_controller(driver_dir: str, controller: str) -> bool:
    try:
        with open(f"{driver_dir}/unbind", "w") as f:
            f.write(controller)
        time.sleep(1.0)
        with open(f"{driver_dir}/bind", "w") as f:
            f.write(controller)
        log.info("power-cycled USB controller %s", controller)
        time.sleep(2.5)  # let the bus re-enumerate
        return True
    except OSError as e:
        log.warning("rebind of %s failed: %s", controller, e)
        return False


def power_cycle_radio(spec: str, controller_hint: str | None = None) -> bool:
    """Power-cycle the dongle's USB controller. Resolves the controller from the
    present device, or from `controller_hint` (config) when the device has
    fallen off the bus. Returns True if a rebind was issued."""
    ids = parse_usb_ids(spec)
    found = controller_for_device(*ids) if ids else None
    if found is None and controller_hint:
        for drv in ("ohci-platform", "ehci-platform"):
            if os.path.exists(f"{_PLATFORM_DRIVERS}/{drv}/{controller_hint}"):
                found = (f"{_PLATFORM_DRIVERS}/{drv}", controller_hint)
                break
    if found is None:
        log.debug("no USB controller to power-cycle for spec %s", spec)
        return False
    return rebind_controller(*found)
