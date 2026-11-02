"""USB OUT path that keeps call audio from blocking everything else.

Bumble's stock UsbPacketSink sends commands, ACL and SCO through ONE queue
gated by ONE in-flight transfer. During a call the SCO stream fills that queue,
so AT commands (hang up, heartbeat) and HCI commands wait behind audio and time
out: the call can't be hung up and the link looks dead. It also keeps a single
isochronous transfer in flight, which starves the audio stream (crackle).

This sink gives SCO its own lane: a small pool of isochronous transfers kept in
flight, and a bounded queue that drops the oldest audio instead of building
latency. Commands and ACL keep the stock path, now never behind audio.
"""

from __future__ import annotations

import asyncio
import collections
import logging

import usb1
from bumble import hci
from bumble.transport import usb as bumble_usb

log = logging.getLogger(__name__)
ISO_TRANSFERS = 4  # in flight at once; Linux wants >= 2 for continuous streaming
MAX_QUEUED_SCO = 50  # ~ a few hundred ms of CVSD; older audio is dropped


class ScoLaneSink(bumble_usb.UsbPacketSink):
    def __init__(self, device, bulk_out, isochronous_out, interface_number=0) -> None:
        super().__init__(device, bulk_out, isochronous_out, interface_number)
        self._free: asyncio.Queue[usb1.USBTransfer] = asyncio.Queue()
        self._in_flight: set[usb1.USBTransfer] = set()
        self._sco: collections.deque[bytes] = collections.deque(maxlen=MAX_QUEUED_SCO)
        self._sco_ready = asyncio.Event()
        self._sco_task: asyncio.Task | None = None
        self._drained = asyncio.Event()
        if self.isochronous_out_transfer is not None:
            mps = isochronous_out.getMaxPacketSize()
            iso_packets = bumble_usb.MAX_SCO_PACKET_SIZE // mps if mps else 1
            self._free.put_nowait(self.isochronous_out_transfer)
            for _ in range(ISO_TRANSFERS - 1):
                self._free.put_nowait(device.getTransfer(iso_packets=iso_packets))
            # The stock path must never touch SCO transfers again (incl. terminate).
            self.isochronous_out_transfer = None
            self._has_iso = True
        else:
            self._has_iso = False

    def start(self) -> None:
        super().start()
        if self._has_iso:
            self._sco_task = asyncio.create_task(self._sco_loop())

    def on_packet(self, packet: bytes) -> None:
        if not self.closed and packet and packet[0] == hci.HCI_SYNCHRONOUS_DATA_PACKET:
            if self._has_iso:
                self._sco.append(packet)  # deque(maxlen) drops the oldest
                self._sco_ready.set()
            return
        super().on_packet(packet)

    async def _sco_loop(self) -> None:
        mps = self.isochronous_out.getMaxPacketSize()
        while not self.closed:
            if not self._sco:
                self._sco_ready.clear()
                await self._sco_ready.wait()
                continue
            transfer = await self._free.get()
            if not self._sco:
                self._free.put_nowait(transfer)
                continue
            payload = self._sco.popleft()[1:]
            lengths = [mps] * ((len(payload) - 1) // mps)
            lengths.append(len(payload) - sum(lengths))
            try:
                transfer.setIsochronous(
                    self.isochronous_out.getAddress(),
                    payload,
                    callback=self._iso_done,
                    iso_transfer_length_list=lengths,
                )
                transfer.submit()
                self._in_flight.add(transfer)
            except Exception as e:
                log.warning("SCO submit failed: %s", e)
                self._free.put_nowait(transfer)

    def _iso_done(self, transfer: usb1.USBTransfer) -> None:
        # libusb event thread -> hand the transfer back on the asyncio loop
        bumble_usb._safe_call_soon(self.loop, self._recycle, transfer)

    def _recycle(self, transfer: usb1.USBTransfer) -> None:
        self._in_flight.discard(transfer)
        if not self._in_flight:
            self._drained.set()
        if not self.closed:
            self._free.put_nowait(transfer)

    async def terminate(self) -> None:
        # Cancel in-flight audio and WAIT for libusb to hand every transfer
        # back. Closing the device with ISO transfers still pending wedges the
        # kernel USB stack (process stuck in D state, only a reboot clears it).
        self.closed = True
        if self._sco_task:
            self._sco_task.cancel()
        self._sco.clear()
        if self._in_flight:
            self._drained.clear()
            for transfer in list(self._in_flight):
                try:
                    transfer.cancel()
                except usb1.USBError:
                    self._in_flight.discard(transfer)  # already completed
            if self._in_flight:
                try:
                    await asyncio.wait_for(self._drained.wait(), timeout=2)
                except TimeoutError:
                    log.warning("%d SCO transfers did not cancel", len(self._in_flight))
        await super().terminate()


def install() -> None:
    """Make Bumble's USB transport use the SCO-lane sink (idempotent).

    Don't shrink Bumble's ISO IN batches (MAX_SCO_IN_PACKETS): 4 x 16 ms on
    this OHCI port stalled ACL even with no call up."""
    bumble_usb.UsbPacketSink = ScoLaneSink
