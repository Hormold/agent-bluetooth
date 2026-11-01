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

    @classmethod
    async def shared(cls, ctx: Context) -> BumbleRadio:
        return await ctx.resource("bumble", lambda: cls.open(ctx.config.get("bluetooth", {})))

    @classmethod
    async def open(cls, cfg: dict[str, Any]) -> BumbleRadio:
        spec = cfg.get("transport", "usb:0")
        keystore = Path(cfg.get("keystore", "~/.agent-bluetooth/keys.json")).expanduser()
        keystore.parent.mkdir(parents=True, exist_ok=True)
        log.info("opening Bluetooth transport %s", spec)
        transport = await open_transport(spec)
        config = DeviceConfiguration(
            name=cfg.get("name", "Agent Bluetooth"),
            class_of_device=CLASS_HANDS_FREE,
            keystore=f"JsonKeyStore:{keystore}",
            classic_enabled=True,
            le_enabled=False,
        )
        device = Device.from_config_with_hci(config, transport.source, transport.sink)
        await device.power_on()
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

    async def connect(self, address: str) -> Connection:
        connection = await self.device.connect(address, transport=BT_BR_EDR_TRANSPORT)
        await connection.authenticate()
        await connection.encrypt()
        return connection

    async def close(self) -> None:
        await asyncio.wait_for(self.transport.close(), 3)
