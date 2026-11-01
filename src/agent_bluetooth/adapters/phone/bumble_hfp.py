"""Phone adapter: this box becomes a Bluetooth hands-free kit (HFP "HF" role).

The phone sees a car kit / headset. It sends call state (+CIEV), caller id
(+CLIP / +CLCC) and call audio (SCO). We send ATA / AT+CHUP / ATD.
Audio: CVSD narrowband, 8 kHz 16-bit PCM over HCI (the controller does the
codec). mSBC wideband needs a software codec and is not enabled yet.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

from bumble import hci, hfp, rfcomm
from bumble.device import Connection, ScoLink

from ...core.context import Context
from ...core.events import E
from ...core.ports import AudioPipe, Call
from ..bumble.radio import BumbleRadio

log = logging.getLogger(__name__)
CVSD_RATE = 8000
Ind = hfp.AgIndicator
Setup = hfp.CallSetupAgIndicator


class BumbleHfpPhone:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.ctx = ctx
        self.bus = ctx.bus
        self.reconnect_every = float(cfg.get("reconnect_every", 25))
        self.reconcile_every = float(cfg.get("reconcile_every", 10))
        self._cind_failures = 0
        self.radio: BumbleRadio | None = None
        self.hf: hfp.HfProtocol | None = None
        self.connection: Connection | None = None
        self.call: Call | None = None
        self.pipe: AudioPipe | None = None
        self.indicators: dict[str, int] = {}
        self._tasks: list[asyncio.Task] = []
        self.config = hfp.HfConfiguration(
            supported_hf_features=[
                hfp.HfFeature.THREE_WAY_CALLING,
                hfp.HfFeature.CLI_PRESENTATION_CAPABILITY,
                hfp.HfFeature.REMOTE_VOLUME_CONTROL,
                hfp.HfFeature.ENHANCED_CALL_STATUS,
                hfp.HfFeature.ESCO_S4_SETTINGS_SUPPORTED,
            ],
            supported_hf_indicators=[],
            supported_audio_codecs=[hfp.AudioCodec.CVSD],
        )

    # lifecycle -------------------------------------------------------------
    async def start(self) -> None:
        # Bring the radio up in the background and keep retrying: a wedged dongle
        # must never block startup, and it should recover on its own.
        self._tasks.append(asyncio.create_task(self._bring_up()))

    async def _bring_up(self) -> None:
        while self.radio is None:
            try:
                self.radio = await BumbleRadio.shared(self.ctx)
            except Exception as e:
                log.warning("radio bring-up failed (%s); retrying in 10s", e)
                await asyncio.sleep(10)
                continue
            channel = self.radio.rfcomm.listen(self._on_dlc)
            self.radio.add_sdp_record(lambda h, ch=channel: hfp.make_hf_sdp_records(h, ch, self.config))
            self._tasks.append(asyncio.create_task(self._reconnect_loop()))
            log.info("hands-free ready on RFCOMM channel %s; pair your phone with it", channel)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.connection:
            try:
                await asyncio.wait_for(self.connection.disconnect(), timeout=3)
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        return {
            "adapter": "bumble-hfp",
            "connected": self.hf is not None,
            "peer": str(self.connection.peer_address) if self.connection else None,
            "call": self.call.to_dict() if self.call else None,
            "indicators": self.indicators,
            "audio": self.pipe is not None,
        }

    def audio(self) -> AudioPipe | None:
        return self.pipe

    # commands --------------------------------------------------------------
    # Every command is time-boxed: a flaky controller must never hang a caller
    # (API handler, automation, router). On timeout we log and move on; the
    # reconcile loop and reconnect bring state back to reality.
    async def _cmd(self, what: str, coro, timeout: float = 8) -> bool:
        if self.hf is None:
            raise RuntimeError("phone not connected")
        try:
            await asyncio.wait_for(coro, timeout)
            return True
        except TimeoutError:
            log.warning("HFP command %s timed out", what)
        except Exception:
            log.warning("HFP command %s failed", what, exc_info=True)
        return False

    async def answer(self) -> None:
        log.info("answering call (ATA)")
        ok = await self._cmd("answer", self._hf().answer_incoming_call())
        log.info("ATA sent ok=%s", ok)

    async def hangup(self) -> None:
        ringing = self.call and self.call.state == "ringing"
        hf = self._hf()
        await self._cmd("hangup", hf.reject_incoming_call() if ringing else hf.terminate_call())

    async def dial(self, number: str) -> None:
        # ATD only returns OK once the AG accepts the request; time-box it so a
        # number that never connects can't wedge the command path.
        await self._cmd("dial", self._hf().execute_command(f"ATD{number};"), timeout=12)

    async def send_dtmf(self, digits: str) -> None:
        for d in digits:
            await self._cmd("dtmf", self._hf().execute_command(f"AT+VTS={d}"))

    async def set_volume(self, level: int) -> None:
        await self._cmd("volume", self._hf().execute_command(f"AT+VGS={max(0, min(15, level))}"))

    def _hf(self) -> hfp.HfProtocol:
        if self.hf is None:
            raise RuntimeError("phone not connected")
        return self.hf

    # connection ------------------------------------------------------------
    def _on_dlc(self, dlc: rfcomm.DLC) -> None:
        """Phone opened the HFP channel to us (fresh pairing or phone-side reconnect)."""
        asyncio.create_task(self._setup(dlc))

    async def _reconnect_loop(self) -> None:
        """Mostly we just stay connectable and let the PHONE reconnect to us (how
        a car kit normally comes back). We only page the phone ourselves as an
        occasional fallback — and with a SHORT page, because while the controller
        is paging it can't page-scan, i.e. it can't accept the phone's own
        reconnect. Paging too aggressively is what blocks 'Connect' on the phone.
        So: long idle windows (connectable), short infrequent outbound tries."""
        assert self.radio
        while True:
            # stay connectable so the phone can reconnect to us during the idle window
            try:
                await self.radio.set_connectable(True)
            except Exception:
                pass
            await asyncio.sleep(self.reconnect_every)
            if self.hf is None:
                for address in await self.radio.bonded_addresses():
                    if self.hf is not None:
                        break
                    try:
                        await asyncio.wait_for(self._try_reconnect(address), timeout=8)
                    except TimeoutError:
                        log.debug("reconnect page to %s timed out", address)
                    except Exception as e:
                        log.debug("reconnect to %s failed: %s", address, e)

    async def _try_reconnect(self, address: str) -> None:
        conn = await self.radio.connect(address)
        found = await hfp.find_ag_sdp_record(conn)
        if not found:
            return
        mux = await self.radio.multiplexer(conn)
        await self._setup(await mux.open_dlc(found[0]))

    async def _setup(self, dlc: rfcomm.DLC) -> None:
        assert self.radio
        conn: Connection = dlc.multiplexer.l2cap_channel.connection
        self.radio.remember_mux(conn, dlc.multiplexer)
        self.connection = conn
        hf = hfp.HfProtocol(dlc, self.config)
        conn.on("sco_request", functools.partial(self._on_sco_request, conn))
        conn.on("sco_connection", self._on_sco)
        conn.on(conn.EVENT_DISCONNECTION, self._on_disconnect)
        await self._disable_sniff(conn)
        hf.on(hf.EVENT_AG_INDICATOR, lambda ind: asyncio.create_task(self._on_indicator(ind)))
        hf.on(hf.EVENT_CLI_NOTIFICATION, self._on_clip)
        hf.on(hf.EVENT_SPEAKER_VOLUME, lambda v: self.bus.publish(E.PHONE_STATUS, speaker_volume=v))

        await asyncio.wait_for(hf.initiate_slc(), timeout=15)
        self.hf = hf
        self._tasks.append(asyncio.create_task(hf.run()))
        self._tasks.append(asyncio.create_task(self._reconcile_loop(hf)))
        for state in hf.ag_indicators:
            self.indicators[state.indicator.value] = state.current_status
        address, name = str(conn.peer_address), (conn.peer_name or "")
        if name:
            self.radio.peer_names[address] = name
        self.bus.publish(E.PHONE_CONNECTED, address=address, name=self.radio.peer_names.get(address, name))
        self._update_call_state()

    # pairing / device management ------------------------------------------
    async def phones(self) -> list[dict[str, Any]]:
        return await self.radio.bonded() if self.radio else []

    async def pair(self, seconds: float = 120) -> None:
        """Open a discovery window so a phone can find and pair with us."""
        if self.radio:
            await self.radio.set_discoverable(True)

    async def forget(self, address: str) -> None:
        if self.radio:
            await self.radio.forget(address)

    async def _reconcile_loop(self, hf: hfp.HfProtocol) -> None:
        """AT heartbeat + self-heal. Every few seconds we read the indicators
        (AT+CIND?). Success re-derives call state, so a MISSED +CIEV can't leave
        a 'ghost' active call. Repeated FAILURE means the SLC is a zombie (ACL up
        but no AT responses — common on cheap controllers); we then tear the link
        down so the reconnect loop rebuilds it. This is what makes a dropped call
        recover on its own instead of hanging forever."""
        self._cind_failures = 0
        while self.hf is hf:
            await asyncio.sleep(self.reconcile_every)
            ok = await self._refresh_indicators()
            if ok:
                self._cind_failures = 0
                if self.call is not None and not self.call.number:
                    await self._fetch_number()
            else:
                self._cind_failures += 1
                if self._cind_failures >= 3 and self.hf is hf:
                    log.warning("HFP link unresponsive (%d missed heartbeats); recycling", self._cind_failures)
                    await self._recycle_link()
                    return

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

    async def _recycle_link(self) -> None:
        """Force a dead SLC down so reconnect rebuilds it (and clears state)."""
        conn = self.connection
        self._on_disconnect()  # clears call/audio/hf, publishes PHONE_DISCONNECTED
        if conn is not None:
            try:
                await asyncio.wait_for(conn.disconnect(), timeout=4)
            except Exception:
                pass

    async def _refresh_indicators(self) -> bool:
        if self.hf is None:
            return False
        try:
            resp = await asyncio.wait_for(
                self.hf.execute_command("AT+CIND?", response_type=hfp.AtResponseType.SINGLE), timeout=4
            )
        except Exception:
            return False
        def as_int(x: Any) -> int:
            if isinstance(x, (bytes, bytearray)):
                x = x.decode()
            return int(x)

        try:
            values = [as_int(x) for x in resp.parameters]
        except (TypeError, ValueError):
            return False
        for state, value in zip(self.hf.ag_indicators, values):
            self.indicators[state.indicator.value] = value
        self._update_call_state()
        return True

    def _on_disconnect(self, *_: Any) -> None:
        self.hf = None
        self.connection = None
        self._audio_down()
        self._end_call()
        self.bus.publish(E.PHONE_DISCONNECTED)

    # call state (driven by +CIEV indicators, NOT CLCC) ---------------------
    async def _on_indicator(self, state: hfp.AgIndicatorState) -> None:
        name, value = state.indicator.value, state.current_status
        self.indicators[name] = value
        if state.indicator in (Ind.CALL, Ind.CALL_SETUP, Ind.CALL_HELD):
            self._update_call_state()
        else:
            self.bus.publish(E.PHONE_STATUS, **{name: value})

    def _update_call_state(self) -> None:
        """Derive call state from the call / callsetup indicators. These are
        pushed by the phone and arrive reliably; CLCC is only for the number."""
        call_ind = int(self.indicators.get("call", 0))
        setup = int(self.indicators.get("callsetup", 0))
        if call_ind:
            state = "active"
        elif setup == 1:
            state = "ringing"
        elif setup in (2, 3):
            state = "dialing"
        else:
            self._end_call()
            return
        if self.call is None:
            direction = "out" if setup in (2, 3) else "in"
            self.call = Call(direction=direction, number="", state="")
            asyncio.create_task(self._fetch_number())
        if state != self.call.state:
            self.call.state = state
            event = {"ringing": E.CALL_RINGING, "dialing": E.CALL_DIALING, "active": E.CALL_ACTIVE}[state]
            self.bus.publish(event, call=self.call.to_dict())
            if self.pipe and state == "active":
                self._announce_audio(True)

    async def _fetch_number(self) -> None:
        """Best-effort caller number via CLCC. Never blocks state; if it times
        out (common on this hardware) we just keep whatever +CLIP gave us."""
        if self.hf is None or self.call is None or self.call.number:
            return
        try:
            calls = await asyncio.wait_for(self.hf.query_current_calls(), timeout=3)
        except Exception:
            return
        for info in calls:
            if info.number and self.call is not None:
                self.call.number = info.number
                return

    def _on_clip(self, cli: hfp.CallLineIdentification) -> None:
        if self.call and not self.call.number:
            self.call.number = cli.number
            if cli.alpha:
                self.call.name = cli.alpha

    def _end_call(self) -> None:
        if self.call:
            self.call.state = "ended"
            self.bus.publish(E.CALL_ENDED, call=self.call.to_dict())
            self.call = None

    # audio -----------------------------------------------------------------
    def _on_sco_request(self, conn: Connection, link_type: int) -> None:
        """Accept the phone's audio link with CVSD settings (same as Bumble's example)."""
        params = hfp.ESCO_PARAMETERS[
            hfp.DefaultCodecParameters.SCO_CVSD_D1
            if link_type == hci.HCI_Connection_Complete_Event.LinkType.SCO
            else hfp.DefaultCodecParameters.ESCO_CVSD_S4
        ]
        if conn.device.host.supports_command(hci.HCI_ENHANCED_ACCEPT_SYNCHRONOUS_CONNECTION_REQUEST_COMMAND):
            cmd = hci.HCI_Enhanced_Accept_Synchronous_Connection_Request_Command(
                bd_addr=conn.peer_address, **params.asdict()
            )
        else:
            cmd = hci.HCI_Accept_Synchronous_Connection_Request_Command(
                bd_addr=conn.peer_address,
                transmit_bandwidth=params.transmit_bandwidth,
                receive_bandwidth=params.receive_bandwidth,
                max_latency=params.max_latency,
                voice_setting=int(hci.VoiceSetting(
                    input_sample_size=hci.VoiceSetting.InputSampleSize.SIZE_16_BITS,
                    input_data_format=hci.VoiceSetting.InputDataFormat.TWOS_COMPLEMENT,
                )),
                retransmission_effort=params.retransmission_effort,
                packet_type=params.packet_type,
            )
        conn.cancel_on_disconnection(conn.device.send_async_command(cmd))

    def _on_sco(self, link: ScoLink) -> None:
        if link.air_mode == hci.CodecID.TRANSPARENT:
            log.warning("phone negotiated a transparent (mSBC) link; not supported yet")
            return
        pipe = self.pipe = AudioPipe(CVSD_RATE)
        host = link.device.host

        def on_packet(packet: hci.HCI_SynchronousDataPacket) -> None:
            ok = packet.packet_status == hci.HCI_SynchronousDataPacket.Status.CORRECTLY_RECEIVED_DATA
            data = packet.data if ok else b"\x00" * len(packet.data)
            pipe.push_from_phone(data)
            # SCO is clocked by the phone: answer every packet with the same size.
            host.send_sco_sdu(connection_handle=packet.connection_handle, sdu=pipe.pull_to_phone(len(data)))

        link.sink = on_packet
        link.on(link.EVENT_DISCONNECTION, lambda *_: self._audio_down())
        log.info("call audio up (%s)", link)
        self._announce_audio(True)

    def _audio_down(self) -> None:
        if self.pipe:
            self.pipe.close()
            self.pipe = None
            self._announce_audio(False)

    def _announce_audio(self, up: bool) -> None:
        if self.call:
            self.bus.publish(E.CALL_AUDIO, call=self.call.to_dict(), up=up, sample_rate=CVSD_RATE)
