"""Link supervision for the hands-free kit: bring the HFP service-level
connection up (phone-initiated or ours), keep it honest with an AT heartbeat,
and get it back when it drops — or exit when the controller is beyond saving.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bumble import hci, hfp

from ...core.events import E
from ...core.tasks import Tasks
from ...core.watchdog import fatal

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from bumble import rfcomm
    from bumble.device import Connection

    from ...core.bus import EventBus
    from ..bumble.radio import BumbleRadio
    from .hfp_audio import ScoAudio
    from .hfp_calls import CallTracker
    from .hfp_commands import HfpCommands

log = logging.getLogger(__name__)
MAX_HCI_FAILURES = 2  # failed HCI probes in a row before we call the controller dead
MAX_MISSED_HEARTBEATS = 3  # failed AT+CIND? polls before the SLC counts as a zombie


@dataclass(frozen=True)
class LinkTiming:
    reconnect_every: float  # s of connectable idle between our own reconnect tries
    reconcile_every: float  # s between AT+CIND? heartbeats
    # Lost a working link and couldn't get it back in this long? The clone
    # controller is wedged (seen after a hangup + connection collision):
    # restart the process, which resets the controller on open.
    offline_restart: float

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> LinkTiming:
        return cls(
            reconnect_every=float(cfg.get("reconnect_every", 25)),
            reconcile_every=float(cfg.get("reconcile_every", 10)),
            offline_restart=float(cfg.get("offline_restart", 90)),
        )


class LinkSupervisor:
    def __init__(  # noqa: PLR0913 - the supervisor wires the four HFP parts together
        self,
        bus: EventBus,
        radio: BumbleRadio,
        *,
        config: hfp.HfConfiguration,
        timing: LinkTiming,
        commands: HfpCommands,
        calls: CallTracker,
        audio: ScoAudio,
    ) -> None:
        self.bus = bus
        self.radio = radio
        self.config = config
        self.timing = timing
        self.commands = commands
        self.calls = calls
        self.audio = audio
        self.connection: Connection | None = None
        self._lost_at: float | None = None
        self._cind_failures = 0
        self._hci_failures = 0
        # Last reconnect failure logged per bonded phone: a phone that left the
        # room fails every page the same way, and one line about it is enough.
        self._unreachable: dict[str, str] = {}
        self._tasks = Tasks("hfp-link")

    def start(self) -> None:
        self._own(self._reconnect_loop(), "hfp-reconnect")

    async def stop(self) -> None:
        await self._tasks.aclose()
        if self.connection:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.connection.disconnect(), timeout=3)

    def _own(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        self._tasks.spawn(coro, name=name)

    def on_dlc(self, dlc: rfcomm.DLC) -> None:
        """Phone opened the HFP channel to us (fresh pairing or phone-side reconnect)."""
        self._own(self._setup(dlc), "hfp-setup")

    # reconnect -------------------------------------------------------------
    async def _reconnect_loop(self) -> None:
        """Mostly we just stay connectable and let the PHONE reconnect to us (how
        a car kit normally comes back). We only page the phone ourselves as an
        occasional fallback — and with a SHORT page, because while the controller
        is paging it can't page-scan, i.e. it can't accept the phone's own
        reconnect. Paging too aggressively is what blocks 'Connect' on the phone.
        So: long idle windows (connectable), short infrequent outbound tries."""
        self._hci_failures = 0
        while True:
            await self._probe_hci()
            await asyncio.sleep(self.timing.reconnect_every)
            if self.commands.hf is not None:
                continue
            self._check_offline_deadline()
            await self._reconnect_bonded()

    async def _probe_hci(self) -> None:
        """Stay connectable so the phone can reconnect to us during the idle
        window. This is also our HCI health probe: if the controller stops
        answering commands, nothing in-process can fix it."""
        try:
            await asyncio.wait_for(self.radio.device.set_connectable(True), timeout=5)
        except Exception as e:  # hardware I/O: any failure counts as a missed probe
            self._hci_failures += 1
            log.warning(
                "HCI not responding (%s), %d in a row",
                e or type(e).__name__,
                self._hci_failures,
                exc_info=True,
            )
            if self._hci_failures >= MAX_HCI_FAILURES:
                fatal(5, "controller stopped answering HCI commands")
        else:
            self._hci_failures = 0

    def _check_offline_deadline(self) -> None:
        limit = self.timing.offline_restart
        if self._lost_at and asyncio.get_running_loop().time() - self._lost_at > limit:
            fatal(5, f"phone link lost {limit:.0f}s ago and not recovered")

    async def _reconnect_bonded(self) -> None:
        try:
            addresses = await asyncio.wait_for(self.radio.bonded_addresses(), timeout=5)
        except Exception:  # keystore/controller trouble: skip this round, retry next
            log.debug("could not list bonded phones", exc_info=True)
            return
        for address in addresses:
            if self.commands.hf is not None:
                return
            try:
                await self._try_reconnect(address)
            except Exception as e:  # the reconnect loop must survive any link failure
                log.debug("reconnect to %s traceback", address, exc_info=True)
                self._note_reconnect_failure(address, e)

    def _note_reconnect_failure(self, address: str, error: Exception) -> None:
        """INFO when a phone's reconnect failure is new or CHANGED (out of range,
        then bad keys or a broken HFP setup, must stay visible); the same failure
        again goes to DEBUG until the phone connects."""
        reason = (
            "timed out" if isinstance(error, TimeoutError) else str(error) or type(error).__name__
        )
        if self._unreachable.get(address) == reason:
            log.debug("reconnect to %s failed again: %s", address, reason)
            return
        self._unreachable[address] = reason
        if isinstance(error, TimeoutError) or "PAGE_TIMEOUT" in reason:
            log.info("phone %s out of reach (%s); still listening for it", address, reason)
        else:
            log.warning("reconnect to %s failed: %s", address, reason)

    async def _try_reconnect(self, address: str) -> None:
        # Short timeout only for paging (keeps the radio free to accept the
        # phone's own reconnect); once linked, give the slow parts — auth,
        # encryption, SDP, RFCOMM, HFP handshake — their own generous budget.
        conn = await asyncio.wait_for(self.radio.connect(address), timeout=12)
        await asyncio.wait_for(self._link_hfp(conn), timeout=25)

    async def _link_hfp(self, conn: Connection) -> None:
        found = await hfp.find_ag_sdp_record(conn)
        if not found:
            raise RuntimeError("phone exposes no HFP audio gateway")
        mux = await self.radio.multiplexer(conn)
        await self._setup(await mux.open_dlc(found[0]))

    # service-level connection ----------------------------------------------
    async def _setup(self, dlc: rfcomm.DLC) -> None:
        radio = self.radio
        conn: Connection = dlc.multiplexer.l2cap_channel.connection
        radio.remember_mux(conn, dlc.multiplexer)
        self.connection = conn
        hf = hfp.HfProtocol(dlc, self.config)
        conn.on("sco_request", functools.partial(self.audio.on_sco_request, conn))
        conn.on("sco_connection", self.audio.on_sco)
        conn.on(conn.EVENT_DISCONNECTION, functools.partial(self._on_connection_lost, conn))
        await self._disable_sniff(conn)
        hf.on(
            hf.EVENT_AG_INDICATOR,
            lambda ind: self._own(self.calls.on_indicator(ind), "hfp-indicator"),
        )
        hf.on(hf.EVENT_CLI_NOTIFICATION, self.calls.on_clip)
        hf.on(
            hf.EVENT_SPEAKER_VOLUME,
            lambda v: self.bus.publish(E.PHONE_STATUS, speaker_volume=v),
        )

        # Bumble's SLC handshake runs its AT commands on the 1 s default reply
        # timeout; this outer budget only bounds the whole exchange.
        await asyncio.wait_for(hf.initiate_slc(), timeout=15)
        self.commands.hf = hf
        self._own(hf.run(), "hfp-run")
        self._own(self._reconcile_loop(hf), "hfp-reconcile")
        address, name = str(conn.peer_address), (conn.peer_name or "")
        if name:
            radio.peer_names[address] = name
        self._lost_at = None
        self._unreachable.pop(address, None)
        self.bus.publish(
            E.PHONE_CONNECTED,
            address=address,
            name=radio.peer_names.get(address, name),
        )
        self.calls.load(hf.ag_indicators)

    async def _disable_sniff(self, conn: Connection) -> None:
        """Keep the ACL link in active mode. Cheap CSR clones often let the link
        drop into sniff (power-save) and then die; disabling link policy keeps it
        alive, which stabilises an otherwise flaky HFP."""
        try:
            await asyncio.wait_for(
                conn.device.send_command(
                    hci.HCI_Write_Link_Policy_Settings_Command(
                        connection_handle=conn.handle, link_policy_settings=0
                    )
                ),
                timeout=4,
            )
        except Exception:
            log.debug("could not disable link policy", exc_info=True)

    # heartbeat -------------------------------------------------------------
    async def _reconcile_loop(self, hf: hfp.HfProtocol) -> None:
        """AT heartbeat + self-heal. Every few seconds we read the indicators
        (AT+CIND?). Success re-derives call state, so a MISSED +CIEV can't leave
        a 'ghost' active call. Repeated FAILURE means the SLC is a zombie (ACL up
        but no AT responses — common on cheap controllers); we then tear the link
        down so the reconnect loop rebuilds it. This is what makes a dropped call
        recover on its own instead of hanging forever."""
        self._cind_failures = 0
        while self.commands.hf is hf:
            await asyncio.sleep(self.timing.reconcile_every)
            values = await self.commands.read_indicators()
            if values is not None:
                self._cind_failures = 0
                self.calls.load(hf.ag_indicators, values)
                if self.calls.call is not None and not self.calls.call.number:
                    await self.calls.fetch_number()
            elif self.audio.flowing():
                # Call audio is arriving, so the link is alive; AT replies are
                # just slow under load. Recycling here would kill a good call.
                self._cind_failures = 0
            else:
                self._cind_failures += 1
                if self._cind_failures >= MAX_MISSED_HEARTBEATS and self.commands.hf is hf:
                    log.warning(
                        "HFP link unresponsive (%d missed heartbeats); recycling",
                        self._cind_failures,
                    )
                    await self._recycle_link()
                    return

    async def _recycle_link(self) -> None:
        """Force a dead SLC down so reconnect rebuilds it (and clears state)."""
        conn = self.connection
        self.on_disconnect()  # clears call/audio/hf, publishes PHONE_DISCONNECTED
        if conn is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.disconnect(), timeout=4)

    def _on_connection_lost(self, conn: Connection, *_: object) -> None:
        """ACL went down. Ignore a stale one (already recycled, or replaced by a
        newer link) so each loss is reported exactly once."""
        if conn is self.connection:
            self.on_disconnect()

    def on_disconnect(self) -> None:
        if self.commands.hf is not None and self._lost_at is None:
            self._lost_at = asyncio.get_running_loop().time()
        self.commands.hf = None
        self.connection = None
        self.audio.down()
        self.calls.end()
        self.bus.publish(E.PHONE_DISCONNECTED)
