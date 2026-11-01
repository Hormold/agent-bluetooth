"""One Bluetooth radio shared by every Bumble-based adapter (HFP calls, MAP SMS).

Bumble is a pure-Python Bluetooth stack: no BlueZ, oFono or PipeWire needed.
Transport examples (see https://google.github.io/bumble/transports/):
  usb:0          first USB dongle (macOS / Windows / Linux)
  hci-socket:0   Linux built-in adapter, with bluetoothd stopped for it
  serial:/dev/ttyAMA0,115200   UART controllers
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bumble import rfcomm, sdp
from bumble.core import BT_BR_EDR_TRANSPORT
from bumble.device import Connection, Device, DeviceConfiguration
from bumble.transport import open_transport

from ...core.context import Context

log = logging.getLogger(__name__)
CLASS_HANDS_FREE = 0x240408  # services: Audio + Rendering; device: Audio/Video, Hands-free


class BumbleRadio:
    def __init__(self, device: Device, transport: Any) -> None:
        self.device = device
        self.transport = transport
        self.rfcomm = rfcomm.Server(device)
        self._muxes: dict[int, rfcomm.Multiplexer] = {}
        self._next_sdp_handle = 0x00010001
        self.peer_names: dict[str, str] = {}  # address -> friendly name, learned on connect

    @classmethod
    async def shared(cls, ctx: Context) -> BumbleRadio:
        return await ctx.resource("bumble", lambda: cls.open(ctx.config.get("bluetooth", {})))

    @classmethod
    async def open(cls, cfg: dict[str, Any]) -> BumbleRadio:
        """Bring the radio up, retrying through a USB controller power-cycle if
        the dongle is wedged — so it recovers on its own with no replug."""
        spec = cfg.get("transport", "usb:0")
        # NOTE: we do NOT power-cycle the USB controller from inside the running
        # daemon — doing so while libusb holds the device knocks the dongle off
        # the bus and can leave a stuck (D-state) process. Controller recovery is
        # the job of recover-usb.sh (systemd ExecStartPre), which runs when no
        # process holds the device. Here we just retry a plain open.
        attempts = int(cfg.get("open_attempts", 3))
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await cls._open_once(cfg, spec)
            except Exception as e:
                last_error = e
                log.warning("radio open attempt %d/%d failed: %s", attempt, attempts, e)
                if attempt < attempts:
                    await asyncio.sleep(2)
        raise RuntimeError(f"could not bring up radio on {spec}") from last_error

    @classmethod
    async def _open_once(cls, cfg: dict[str, Any], spec: str) -> BumbleRadio:
        keystore = Path(cfg.get("keystore", "~/.agent-bluetooth/keys.json")).expanduser()
        keystore.parent.mkdir(parents=True, exist_ok=True)
        log.info("opening Bluetooth transport %s", spec)
        transport = await asyncio.wait_for(open_transport(spec), timeout=15)
        config = DeviceConfiguration(
            name=cfg.get("name", "Agent Bluetooth"),
            class_of_device=CLASS_HANDS_FREE,
            keystore=f"JsonKeyStore:{keystore}",
            classic_enabled=True,
            le_enabled=False,
        )
        device = Device.from_config_with_hci(config, transport.source, transport.sink)
        await asyncio.wait_for(device.power_on(), timeout=15)
        await device.set_discoverable(bool(cfg.get("discoverable", True)))
        await device.set_connectable(True)
        log.info("radio up: %s (%s)", config.name, device.public_address)
        return cls(device, transport)

    def add_sdp_record(self, make: Callable[[int], list[sdp.ServiceAttribute]]) -> int:
        """`make(handle)` builds the record; the handle is embedded in it."""
        handle = self._next_sdp_handle
        self._next_sdp_handle += 1
        self.device.sdp_service_records = {**self.device.sdp_service_records, handle: make(handle)}
        return handle

    def remember_mux(self, connection: Connection, mux: rfcomm.Multiplexer) -> None:
        self._muxes[connection.handle] = mux
        connection.on(connection.EVENT_DISCONNECTION, lambda *_: self._muxes.pop(connection.handle, None))

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
        out = []
        for address in await self.bonded_addresses():
            out.append({
                "address": address,
                "name": self.peer_names.get(address, ""),
                "connected": address in connected,
            })
        return out

    async def forget(self, address: str) -> None:
        for c in list(self.device.connections.values()):
            if str(c.peer_address) == address:
                try:
                    await c.disconnect()
                except Exception:
                    pass
        if self.device.keystore is not None:
            await self.device.keystore.delete(address)
        self.peer_names.pop(address, None)

    async def set_discoverable(self, on: bool) -> None:
        await self.device.set_discoverable(on)
        await self.device.set_connectable(on or True)

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
        await asyncio.wait_for(self.transport.close(), 3)
