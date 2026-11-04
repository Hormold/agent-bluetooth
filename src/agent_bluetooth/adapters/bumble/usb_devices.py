"""Which USB Bluetooth dongles are plugged in, and where.

A farm runs one daemon per dongle, so each needs a transport that names *its*
dongle and nothing else. Cheap clones share VID:PID and often a serial number,
and `usb:0` indexes shift whenever a dongle is replugged; the USB port path
(`usb:1-1.2`, bus 1, hub port 1, port 2) is the one name that stays put.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import usb1

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)
SYS_USB = Path("/sys/bus/usb/devices")
_BT_HCI = (0xE0, 0x01, 0x01)  # wireless controller / RF / Bluetooth programming
_COMPOSITE = (0x00, 0xEF)  # class defined per interface, or "miscellaneous"


@dataclass(frozen=True)
class UsbRadio:
    path: str  # bus-port path, e.g. "1-1.2"
    vid: int
    pid: int
    serial: str
    product: str

    @property
    def transport(self) -> str:
        return f"usb:{self.path}+sco=2"

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "id": f"{self.vid:04x}:{self.pid:04x}",
            "serial": self.serial,
            "product": self.product,
            "transport": self.transport,
        }


def device_path(device: usb1.USBDevice) -> str:
    """Bumble's `usb:<bus>-<ports>` moniker, also the Linux sysfs device name."""
    return f"{device.getBusNumber()}-{'.'.join(map(str, device.getPortNumberList()))}"


def list_radios() -> list[UsbRadio]:
    out = []
    with usb1.USBContext() as context:
        for device in context.getDeviceIterator(skip_on_error=True):
            if _is_bluetooth(device):
                out.append(
                    UsbRadio(
                        device_path(device),
                        device.getVendorID(),
                        device.getProductID(),
                        _string(device.getSerialNumber),
                        _string(device.getProduct),
                    )
                )
            device.close()
    return sorted(out, key=lambda r: r.path)


def _is_bluetooth(device: usb1.USBDevice) -> bool:
    triple = (device.getDeviceClass(), device.getDeviceSubClass(), device.getDeviceProtocol())
    if triple == _BT_HCI:
        return True
    if device.getDeviceClass() not in _COMPOSITE:
        return False
    return any(
        (s.getClass(), s.getSubClass(), s.getProtocol()) == _BT_HCI
        for config in device
        for interface in config
        for s in interface
    )


def _string(read: Callable[[], str | None]) -> str:
    """Descriptor strings need an open handle on some OSes; absent is fine."""
    try:
        return read() or ""
    except usb1.USBError:
        return ""


def sysfs_identity(path: str) -> str | None:
    """devnum of the device at a port path: changes on every re-enumeration, so a
    replugged dongle (dead libusb handle) is told apart from the one we opened.
    None when it's gone, or off Linux where there is no sysfs to ask."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        return (SYS_USB / path / "devnum").read_text().strip()
    except OSError:
        return None
