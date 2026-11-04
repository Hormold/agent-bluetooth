"""Call audio of the hands-free kit: accept the phone's SCO/eSCO link and pump
PCM between it and an AudioPipe, on the phone's clock.

CVSD narrowband, 8 kHz 16-bit PCM over HCI (the controller does the codec).
mSBC wideband needs a software codec and is not enabled yet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bumble import hci, hfp

from ...core.events import E
from ...core.ports import AudioPipe

if TYPE_CHECKING:
    from collections.abc import Callable

    from bumble.device import Connection, ScoLink

    from ...core.bus import EventBus
    from ...core.ports import Call

log = logging.getLogger(__name__)
CVSD_RATE = 8000


class ScoAudio:
    def __init__(self, bus: EventBus, current_call: Callable[[], Call | None]) -> None:
        self.bus = bus
        self._current_call = current_call
        self.pipe: AudioPipe | None = None
        self.link_info: dict[str, str] = {}
        self._last_pushed = 0

    def on_sco_request(self, conn: Connection, link_type: int) -> None:
        """Accept the phone's audio link with CVSD settings (same as Bumble's example)."""
        params = hfp.ESCO_PARAMETERS[
            hfp.DefaultCodecParameters.SCO_CVSD_D1
            if link_type == hci.HCI_Connection_Complete_Event.LinkType.SCO
            else hfp.DefaultCodecParameters.ESCO_CVSD_S4
        ]
        if conn.device.host.supports_command(
            hci.HCI_ENHANCED_ACCEPT_SYNCHRONOUS_CONNECTION_REQUEST_COMMAND
        ):
            cmd = hci.HCI_Enhanced_Accept_Synchronous_Connection_Request_Command(
                bd_addr=conn.peer_address, **params.asdict()
            )
        else:
            cmd = hci.HCI_Accept_Synchronous_Connection_Request_Command(
                bd_addr=conn.peer_address,
                transmit_bandwidth=params.transmit_bandwidth,
                receive_bandwidth=params.receive_bandwidth,
                max_latency=params.max_latency,
                voice_setting=int(
                    hci.VoiceSetting(
                        input_sample_size=hci.VoiceSetting.InputSampleSize.SIZE_16_BITS,
                        input_data_format=hci.VoiceSetting.InputDataFormat.TWOS_COMPLEMENT,
                    )
                ),
                retransmission_effort=params.retransmission_effort,
                packet_type=params.packet_type,
            )
        conn.cancel_on_disconnection(conn.device.send_async_command(cmd))

    def on_sco(self, link: ScoLink) -> None:
        if link.air_mode == hci.CodecID.TRANSPARENT:
            log.warning("phone negotiated a transparent (mSBC) link; not supported yet")
            return
        pipe = self.pipe = AudioPipe(CVSD_RATE)
        self._last_pushed = 0
        self.link_info = {
            "codec": "CVSD",
            "link": "eSCO"
            if link.link_type == hci.HCI_Connection_Complete_Event.LinkType.ESCO
            else "SCO",
        }
        host = link.device.host

        def on_packet(packet: hci.HCI_SynchronousDataPacket) -> None:
            ok = (
                packet.packet_status == hci.HCI_SynchronousDataPacket.Status.CORRECTLY_RECEIVED_DATA
            )
            data = packet.data if ok else b"\x00" * len(packet.data)
            pipe.push_from_phone(data)
            # SCO is clocked by the phone: answer every packet with the same size.
            host.send_sco_sdu(
                connection_handle=packet.connection_handle,
                sdu=pipe.pull_to_phone(len(data)),
            )

        link.sink = on_packet
        link.on(link.EVENT_DISCONNECTION, lambda *_: self.down())
        log.info("call audio up (%s)", link)
        self.announce(up=True)

    def flowing(self) -> bool:
        """True if the phone delivered call audio since the last check."""
        pipe = self.pipe
        if pipe is None:
            return False
        flowing = pipe.pushed > self._last_pushed
        self._last_pushed = pipe.pushed
        return flowing

    def down(self) -> None:
        if self.pipe:
            self.pipe.close()
            self.pipe = None
            self.announce(up=False)

    def announce(self, *, up: bool) -> None:
        if call := self._current_call():
            self.bus.publish(E.CALL_AUDIO, call=call.to_dict(), up=up, sample_rate=CVSD_RATE)
