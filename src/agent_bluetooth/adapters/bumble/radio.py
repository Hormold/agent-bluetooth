"""One Bluetooth radio shared by every Bumble-based adapter (HFP calls, MAP SMS).

Bumble is a pure-Python Bluetooth stack: no BlueZ, oFono or PipeWire needed.
Transport examples (see https://google.github.io/bumble/transports/):
  usb:0          first USB dongle (macOS / Windows / Linux)
  hci-socket:0   Linux built-in adapter, with bluetoothd stopped for it
  serial:/dev/ttyAMA0,115200   UART controllers
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bumble import rfcomm
from bumble.core import BT_BR_EDR_TRANSPORT
from bumble.device import Device, DeviceConfiguration
from bumble.transport import open_transport
from bumble.transport.common import TransportInitError

from ...core.context import ResourceKey
from ...core.tasks import Tasks
from ...core.watchdog import fatal
from . import usb_devices, usb_sink

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from bumble import sdp
    from bumble.device import Connection
    from bumble.transport.common import Transport

    from ...core.context import Context

log = logging.getLogger(__name__)
RTK_FIRMWARE_ENV = "BUMBLE_RTK_FIRMWARE_DIR"
DEFAULT_FIRMWARE_DIR = "~/.agent-bluetooth/rtl_bt"  # shared by every instance on the host


class RadioProblem:
    """Why the radio isn't up, in words a person or an agent can act on.
    Exposed in phone status as `radio_error` ({error, hint}). Deliberately no
    auto-fixing: dongles and systems differ, so we explain and point the way.
    One per daemon (a Context resource), never process-global."""

    def __init__(self) -> None:
        self.error: str | None = None
        self.hint: str | None = None

    @classmethod
    def of(cls, ctx: Context) -> RadioProblem:
        return ctx.sync_resource(ResourceKey("radio_problem", cls), cls)

    def set(self, error: str, spec: str) -> None:
        self.error, self.hint = error, _hint(error, spec)

    def clear(self) -> None:
        self.error = self.hint = None

    def as_dict(self) -> dict[str, str] | None:
        return {"error": self.error, "hint": self.hint or ""} if self.error else None


def _hint(error: str, spec: str) -> str:
    e = error.lower()
    if "firmware" in e:
        return (
            "This dongle needs a vendor firmware file before it works. Find the named file "
            "(Realtek: linux-firmware, rtl_bt/) and put it in the directory set by "
            "BUMBLE_RTK_FIRMWARE_DIR. See https://google.github.io/bumble/drivers/realtek.html"
        )
    if "busy" in e or "access" in e:
        return (
            "Another driver or process holds the dongle. Linux: stop bluetoothd or unbind the "
            "kernel btusb driver for this device; make sure this user can access USB devices."
        )
    if (
        "not found" in e
        or "not_found" in e
        or "no such device" in e
        or "no device" in e
        or "no_device" in e
    ):
        return (
            f"No USB Bluetooth dongle matched transport '{spec}'. Check it is plugged in; "
            "`agent-bluetooth radios` lists the dongles present with a transport for each, "
            "for [bluetooth] transport. Waiting: it comes up by itself once plugged in."
        )
    return f"Opening '{spec}' failed. Check the dongle and [bluetooth] transport, then run agent-bluetooth logs."


class _FirmwareWarnings(logging.Handler):
    """Bumble's vendor drivers only log a warning when firmware is missing and
    keep going with a half-working radio. Turn that into a visible problem.
    Attached only while this radio opens, so it can't blame another dongle."""

    def __init__(self, problem: RadioProblem, spec: str) -> None:
        super().__init__(logging.WARNING)
        self.problem, self.spec = problem, spec

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "Firmware file" in msg and "not found" in msg:
            self.problem.set(msg, self.spec)


@contextlib.contextmanager
def _watch_firmware(problem: RadioProblem, spec: str) -> Generator[None]:
    handler = _FirmwareWarnings(problem, spec)
    logger = logging.getLogger("bumble.drivers")
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)


CLASS_HANDS_FREE = 0x240408  # services: Audio + Rendering; device: Audio/Video, Hands-free
OPEN_RETRY_DELAY = 2.0  # s between radio open attempts; lets a released USB claim settle


def _prepare_keystore(path: str) -> Path:
    keystore = Path(path).expanduser()
    keystore.parent.mkdir(parents=True, exist_ok=True)
    return keystore


class RadioAbsentError(RuntimeError):
    """No device matches the transport: unplugged, or another port. Not a fault
    to restart over; the caller waits for it to appear."""

    def __init__(self, spec: str) -> None:
        super().__init__(f"no Bluetooth dongle at {spec}")
        self.spec = spec


def _use_firmware_dir(cfg: dict[str, Any]) -> None:
    """Point Bumble's Realtek driver at our firmware folder. Without it a
    RTL8761B dongle (TP-Link UB500 etc.) comes up half-working: "radio up", but
    with ROM firmware that drops links. An explicit env var always wins."""
    if RTK_FIRMWARE_ENV in os.environ:
        return
    folder = Path(cfg.get("firmware_dir", DEFAULT_FIRMWARE_DIR)).expanduser()
    if folder.is_dir():
        os.environ[RTK_FIRMWARE_ENV] = str(folder)


class BumbleRadio:
    def __init__(self, device: Device, transport: Transport, *, discoverable: bool) -> None:
        self.device = device
        self.transport = transport
        self.discoverable_baseline = discoverable  # outside a pairing window
        self.rfcomm = rfcomm.Server(device)
        self._muxes: dict[int, rfcomm.Multiplexer] = {}
        self._next_sdp_handle = 0x00010001
        self.peer_names: dict[str, str] = {}  # address -> friendly name, learned on connect
        self._tasks = Tasks(f"radio:{device.public_address}")

    @classmethod
    async def shared(cls, ctx: Context) -> BumbleRadio:
        return await ctx.resource(
            ResourceKey("bumble", cls),
            lambda: cls.open(ctx.config.get("bluetooth", {}), RadioProblem.of(ctx)),
        )

    @classmethod
    async def open(cls, cfg: dict[str, Any], problem: RadioProblem) -> BumbleRadio:
        """Bring the radio up, retrying a plain open a few times; an absent dongle
        raises RadioAbsentError at once."""
        spec = cfg.get("transport", "usb:0")
        # NOTE: we do NOT power-cycle the USB controller from inside the running
        # daemon — doing so while libusb holds the device knocks the dongle off
        # the bus and can leave a stuck (D-state) process. Controller recovery is
        # the job of recover-usb.sh (systemd ExecStartPre), which runs when no
        # process holds the device. Here we just retry a plain open.
        attempts = int(cfg.get("open_attempts", 3))
        last_error: Exception | None = None
        _use_firmware_dir(cfg)
        for attempt in range(1, attempts + 1):
            try:
                radio = await cls._open_reporting(cfg, spec, problem)
            except RadioAbsentError:
                raise  # retrying now won't plug it in; the caller waits
            except Exception as e:  # hardware I/O: any failure is reported and retried
                last_error = e
                log.warning("radio open attempt %d/%d failed: %s", attempt, attempts, e)
                log.debug("radio open failure detail", exc_info=True)
                if attempt < attempts:
                    await asyncio.sleep(OPEN_RETRY_DELAY)
            else:
                return radio
        raise RuntimeError(f"could not bring up radio on {spec}") from last_error

    @classmethod
    async def _open_reporting(
        cls, cfg: dict[str, Any], spec: str, problem: RadioProblem
    ) -> BumbleRadio:
        """One open; its outcome becomes the radio's visible problem (or lack of one)."""
        try:
            with _watch_firmware(problem, spec):
                radio = await cls._open_once(cfg, spec)
        except TransportInitError as e:
            problem.set(str(e), spec)
            if str(e) == "device not found":
                raise RadioAbsentError(spec) from e
            raise
        except Exception as e:
            problem.set(str(e) or type(e).__name__, spec)
            raise
        if problem.error is None or "firmware" not in problem.error.lower():
            problem.clear()
        return radio

    @classmethod
    async def _open_once(cls, cfg: dict[str, Any], spec: str) -> BumbleRadio:
        keystore = await asyncio.to_thread(_prepare_keystore, cfg["keystore"])
        log.debug("opening Bluetooth transport %s", spec)  # polled while unplugged
        usb_sink.install()  # SCO gets its own USB lane (see usb_sink.py)
        transport = await asyncio.wait_for(open_transport(spec), timeout=15)
        config = DeviceConfiguration(
            name=cfg.get("name", "Agent Bluetooth"),
            class_of_device=CLASS_HANDS_FREE,
            keystore=f"JsonKeyStore:{keystore}",
            classic_enabled=True,
            le_enabled=False,
        )
        discoverable = bool(cfg.get("discoverable", True))
        try:
            device = Device.from_config_with_hci(config, transport.source, transport.sink)
            await asyncio.wait_for(device.power_on(), timeout=15)
            await device.set_discoverable(discoverable)
            await device.set_connectable(True)
        except BaseException:
            # Release the USB claim, or every retry in this process gets BUSY.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(transport.close(), timeout=3)
            raise
        log.info("radio up: %s (%s)", config.name, device.public_address)
        radio = cls(device, transport, discoverable=discoverable)
        radio.watch_usb()
        return radio

    def watch_usb(self) -> None:
        self._tasks.spawn(_watch_usb(self.transport), name="usb-watch")

    def add_sdp_record(self, make: Callable[[int], list[sdp.ServiceAttribute]]) -> int:
        """`make(handle)` builds the record; the handle is embedded in it."""
        handle = self._next_sdp_handle
        self._next_sdp_handle += 1
        self.device.sdp_service_records = {
            **self.device.sdp_service_records,
            handle: make(handle),
        }
        return handle

    def remember_mux(self, connection: Connection, mux: rfcomm.Multiplexer) -> None:
        self._muxes[connection.handle] = mux
        connection.on(
            connection.EVENT_DISCONNECTION,
            lambda *_: self._muxes.pop(connection.handle, None),
        )

    async def multiplexer(self, connection: Connection) -> rfcomm.Multiplexer:
        """RFCOMM session to the phone: reuse the one HFP opened, else start one."""
        if mux := self._muxes.get(connection.handle):
            return mux
        mux = await rfcomm.Client(connection).start()
        self.remember_mux(connection, mux)
        return mux

    async def bonded_addresses(self) -> list[str]:
        if self.device.keystore is None:
            return []
        return [name for name, _ in await self.device.keystore.get_all()]

    def connected_addresses(self) -> set[str]:
        return {str(c.peer_address) for c in self.device.connections.values()}

    async def bonded(self) -> list[dict[str, Any]]:
        """Paired phones with name (if known) and whether they're connected now."""
        connected = self.connected_addresses()
        return [
            {
                "address": address,
                "name": self.peer_names.get(address, ""),
                "connected": address in connected,
            }
            for address in await self.bonded_addresses()
        ]

    async def forget(self, address: str) -> None:
        for c in list(self.device.connections.values()):
            if str(c.peer_address) == address:
                with contextlib.suppress(Exception):
                    await c.disconnect()
        if self.device.keystore is not None:
            # KeyError: not bonded with this controller (e.g. after swapping dongles)
            with contextlib.suppress(KeyError):
                await self.device.keystore.delete(address)
        self.peer_names.pop(address, None)

    async def set_discoverable(self, *, on: bool) -> None:
        await self.device.set_discoverable(on)
        await self.device.set_connectable(True)  # always: the phone must reconnect

    def existing_connection(self, address: str) -> Connection | None:
        for c in self.device.connections.values():
            if str(c.peer_address) == address:
                return c
        return None

    async def connect(self, address: str) -> Connection:
        # Reuse a live ACL link if one already exists — a fresh connect would
        # fail with CONNECTION_ALREADY_EXISTS and leave reconnect stuck.
        if (existing := self.existing_connection(address)) is not None:
            return existing
        try:
            connection = await self.device.connect(address, transport=BT_BR_EDR_TRANSPORT)
        except Exception:
            if (existing := self.existing_connection(address)) is not None:
                return existing
            raise
        await connection.authenticate()
        await connection.encrypt()
        return connection

    async def close(self) -> None:
        await self._tasks.aclose(timeout=1)  # the watcher must not see the close as a lost dongle
        await asyncio.wait_for(self.transport.close(), 3)


async def _watch_usb(transport: Transport) -> None:
    """If the dongle is unplugged/replugged, our libusb handle is dead forever.
    Detect it (the transport reports termination, or on Linux the device at our
    port path is gone or re-enumerated) and exit, so the supervisor restarts us
    with a fresh handle. This is what makes a physical replug just work. Only
    this daemon's dongle is watched, by port path: siblings in a farm sharing a
    VID:PID can come and go without restarting us."""
    handle = getattr(transport.sink, "device", None)
    path = usb_devices.device_path(handle.getDevice()) if handle is not None else None
    start = usb_devices.sysfs_identity(path) if path else None
    terminated = getattr(transport.source, "terminated", None)
    while True:
        await asyncio.sleep(3)
        now = usb_devices.sysfs_identity(path) if path else None
        dead = terminated is not None and terminated.done()
        if dead or now != start:
            fatal(3, f"USB radio {path} lost (devnum {start} -> {now}, terminated={dead})")
