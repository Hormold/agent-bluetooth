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
        self.reconnect_every = float(cfg.get("reconnect_every", 30))
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
        self.radio = await BumbleRadio.shared(self.ctx)
        channel = self.radio.rfcomm.listen(self._on_dlc)
        self.radio.add_sdp_record(lambda h: hfp.make_hf_sdp_records(h, channel, self.config))
        self._tasks.append(asyncio.create_task(self._reconnect_loop()))
        log.info("hands-free ready on RFCOMM channel %s; pair your phone with it", channel)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.connection:
            await self.connection.disconnect()

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
    async def answer(self) -> None:
        await self._hf().answer_incoming_call()

    async def hangup(self) -> None:
        if self.call and self.call.state == "ringing":
            await self._hf().reject_incoming_call()
        else:
            await self._hf().terminate_call()

    async def dial(self, number: str) -> None:
        await self._hf().execute_command(f"ATD{number};")

    async def send_dtmf(self, digits: str) -> None:
        for d in digits:
            await self._hf().execute_command(f"AT+VTS={d}")

    async def set_volume(self, level: int) -> None:
        await self._hf().execute_command(f"AT+VGS={max(0, min(15, level))}")

    def _hf(self) -> hfp.HfProtocol:
        if self.hf is None:
            raise RuntimeError("phone not connected")
        return self.hf

    # connection ------------------------------------------------------------
    def _on_dlc(self, dlc: rfcomm.DLC) -> None:
        """Phone opened the HFP channel to us (fresh pairing or phone-side reconnect)."""
        asyncio.create_task(self._setup(dlc))

    async def _reconnect_loop(self) -> None:
        """After a restart we dial the bonded phone ourselves, like a car does."""
        assert self.radio
        while True:
            if self.hf is None:
                for address in await self.radio.bonded_addresses():
                    try:
                        conn = await self.radio.connect(address)
                        found = await hfp.find_ag_sdp_record(conn)
                        if not found:
                            continue
                        mux = await self.radio.multiplexer(conn)
                        await self._setup(await mux.open_dlc(found[0]))
                        break
                    except Exception as e:  # phone away / BT off: try later
                        log.debug("reconnect to %s failed: %s", address, e)
            await asyncio.sleep(self.reconnect_every)

    async def _setup(self, dlc: rfcomm.DLC) -> None:
        assert self.radio
        conn: Connection = dlc.multiplexer.l2cap_channel.connection
        self.radio.remember_mux(conn, dlc.multiplexer)
        self.connection = conn
        hf = hfp.HfProtocol(dlc, self.config)
        conn.on("sco_request", functools.partial(self._on_sco_request, conn))
        conn.on("sco_connection", self._on_sco)
        conn.on(conn.EVENT_DISCONNECTION, self._on_disconnect)
        hf.on(hf.EVENT_AG_INDICATOR, lambda ind: asyncio.create_task(self._on_indicator(ind)))
        hf.on(hf.EVENT_CLI_NOTIFICATION, self._on_clip)
        hf.on(hf.EVENT_SPEAKER_VOLUME, lambda v: self.bus.publish(E.PHONE_STATUS, speaker_volume=v))

        await hf.initiate_slc()
        self.hf = hf
        self._tasks.append(asyncio.create_task(hf.run()))
        for state in hf.ag_indicators:
            self.indicators[state.indicator.value] = state.current_status
        self.bus.publish(E.PHONE_CONNECTED, address=str(conn.peer_address), name=conn.peer_name or "")
        await self._sync_calls()

    def _on_disconnect(self, *_: Any) -> None:
        self.hf = None
        self.connection = None
        self._audio_down()
        self._end_call()
        self.bus.publish(E.PHONE_DISCONNECTED)

    # call state ------------------------------------------------------------
    async def _on_indicator(self, state: hfp.AgIndicatorState) -> None:
        name, value = state.indicator.value, state.current_status
        self.indicators[name] = value
        if state.indicator in (Ind.CALL, Ind.CALL_SETUP, Ind.CALL_HELD):
            await self._sync_calls()
        else:
            self.bus.publish(E.PHONE_STATUS, **{name: value})

    async def _sync_calls(self) -> None:
        """Ask the phone for the call list (+CLCC): one source of truth for state
        and number, instead of guessing from indicator order."""
        calls = await self._hf().query_current_calls() if self.hf else []
        info = calls[0] if calls else None
        if info is None:
            self._end_call()
            return
        S = hfp.CallInfoStatus
        state = {
            S.INCOMING: "ringing", S.WAITING: "ringing",
            S.DIALING: "dialing", S.ALERTING: "dialing",
        }.get(info.status, "active")
        if self.call is None:
            direction = "in" if info.direction == hfp.CallInfoDirection.MOBILE_TERMINATED_CALL else "out"
            self.call = Call(direction=direction, number=info.number or "", state="")
        if info.number and not self.call.number:
            self.call.number = info.number
        if state != self.call.state:
            self.call.state = state
            event = {"ringing": E.CALL_RINGING, "dialing": E.CALL_DIALING, "active": E.CALL_ACTIVE}[state]
            self.bus.publish(event, call=self.call.to_dict())
            if self.pipe and state == "active":
                self._announce_audio(True)

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
