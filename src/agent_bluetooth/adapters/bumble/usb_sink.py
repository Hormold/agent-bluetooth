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
import contextlib
import logging

import usb1
from bumble import hci
from bumble.transport import usb as bumble_usb

from ...core.tasks import Tasks

log = logging.getLogger(__name__)
ISO_TRANSFERS = 4  # in flight at once; Linux wants >= 2 for continuous streaming
MAX_QUEUED_SCO = 50  # ~ a few hundred ms of CVSD; older audio is dropped
CANCEL_DRAIN_TIMEOUT = 2.0  # s to wait for libusb to return cancelled ISO transfers


class ScoLaneSink(bumble_usb.UsbPacketSink):
    def __init__(
        self,
        device: usb1.USBDeviceHandle,
        bulk_out: usb1.USBEndpoint,
        isochronous_out: usb1.USBEndpoint | None,
        interface_number: int = 0,
    ) -> None:
        super().__init__(device, bulk_out, isochronous_out, interface_number)
        self._free: asyncio.Queue[usb1.USBTransfer] = asyncio.Queue()
        self._in_flight: set[usb1.USBTransfer] = set()
        self._sco: collections.deque[bytes] = collections.deque(maxlen=MAX_QUEUED_SCO)
        self._sco_ready = asyncio.Event()
        self._tasks = Tasks("usb-sco-lane")  # Bumble builds the sink: no Context to borrow
        self._drained = asyncio.Event()
        if self.isochronous_out_transfer is not None and isochronous_out is not None:
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
            self._tasks.spawn(self._sco_loop(), name="usb-sco-lane")

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
            except Exception:  # hardware I/O: a failed submit must not kill the SCO lane
                log.warning("SCO submit failed", exc_info=True)
                self._free.put_nowait(transfer)

    def _iso_done(self, transfer: usb1.USBTransfer) -> None:
        # libusb event thread -> hand the transfer back on the asyncio loop. A
        # callback after the loop closed (teardown) must be a no-op: raising on
        # libusb's C thread can abort the whole process.
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self._recycle, transfer)

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
        await self._tasks.aclose()  # no new SCO submits while in-flight ones are cancelled
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
                    await asyncio.wait_for(self._drained.wait(), timeout=CANCEL_DRAIN_TIMEOUT)
                except TimeoutError:
                    log.warning("%d SCO transfers did not cancel", len(self._in_flight))
        await super().terminate()


def install() -> None:
    """Make Bumble's USB transport use the SCO-lane sink (idempotent).

    Don't shrink Bumble's ISO IN batches (MAX_SCO_IN_PACKETS): 4 x 16 ms on
    this OHCI port stalled ACL even with no call up."""
    # Monkey-patch by design: Bumble builds the sink inside open_usb_transport.
    bumble_usb.UsbPacketSink = ScoLaneSink  # ty: ignore[invalid-assignment] - subclass swap
