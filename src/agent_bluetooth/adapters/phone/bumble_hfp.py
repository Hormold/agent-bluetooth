"""Phone adapter: this box becomes a Bluetooth hands-free kit (HFP "HF" role).

The phone sees a car kit / headset. It sends call state (+CIEV), caller id
(+CLIP / +CLCC) and call audio (SCO). We send ATA / AT+CHUP / ATD.

Split by responsibility:
  hfp_link      bring the link up, heartbeat, reconnect, give up (restart)
  hfp_commands  the AT commands we send, each with a real time budget
  hfp_calls     call state from +CIEV indicators
  hfp_audio     the SCO audio link <-> AudioPipe
  pairing       the discoverable window
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from bumble import hfp

from ...core.tasks import Tasks
from ...core.watchdog import fatal
from ..bumble.radio import BumbleRadio, RadioAbsentError, RadioProblem
from .hfp_audio import ScoAudio
from .hfp_calls import CallTracker
from .hfp_commands import HfpCommands
from .hfp_link import LinkSupervisor, LinkTiming
from .pairing import PairingWindow

if TYPE_CHECKING:
    from ...core.context import Context
    from ...core.ports import AudioPipe, Call

log = logging.getLogger(__name__)
MAX_BRINGUP_FAILURES = 3  # then exit: leaked USB state needs a fresh process
BRINGUP_RETRY_DELAY = 10.0  # s between radio bring-up attempts
ABSENT_POLL = 3.0  # s between looks for an unplugged dongle


class BumbleHfpPhone:
    def __init__(self, ctx: Context, cfg: dict[str, Any]) -> None:
        self.ctx = ctx
        self.bus = ctx.bus
        self.timing = LinkTiming.from_config(cfg)
        self.radio: BumbleRadio | None = None
        self.link: LinkSupervisor | None = None
        self.tasks = Tasks(f"{ctx.bus.instance}:hfp")  # bring-up, CLCC lookups
        self.commands = HfpCommands()
        self.sco = ScoAudio(self.bus, lambda: self.calls.call)
        self.calls = CallTracker(self.bus, self.commands, self.sco, self.tasks)
        self.pairing = PairingWindow()
        self.config = hfp.HfConfiguration(
            supported_hf_features=[
                hfp.HfFeature.THREE_WAY_CALLING,
                hfp.HfFeature.CLI_PRESENTATION_CAPABILITY,
                hfp.HfFeature.REMOTE_VOLUME_CONTROL,
                hfp.HfFeature.ENHANCED_CALL_STATUS,
                hfp.HfFeature.ESCO_S4_SETTINGS_SUPPORTED,
                hfp.HfFeature.VOICE_RECOGNITION_ACTIVATION,
            ],
            supported_hf_indicators=[],
            supported_audio_codecs=[hfp.AudioCodec.CVSD],
        )

    @property
    def call(self) -> Call | None:
        return self.calls.call

    # lifecycle -------------------------------------------------------------
    async def start(self) -> None:
        # Bring the radio up in the background and keep retrying: a wedged dongle
        # must never block startup, and it should recover on its own.
        self.tasks.spawn(self._bring_up(), name="hfp-bring-up")

    async def _bring_up(self) -> None:
        failures = 0
        absent = False
        while self.radio is None:
            try:
                radio = await BumbleRadio.shared(self.ctx)
            except RadioAbsentError as e:
                # Unplugged is a state, not a fault: wait quietly, never restart.
                if not absent:
                    log.warning("%s; waiting for it to be plugged in", e)
                absent = True
                await asyncio.sleep(ABSENT_POLL)
                continue
            except Exception as e:  # hardware bring-up: every failure is retried
                failures += 1
                if failures >= MAX_BRINGUP_FAILURES:
                    # Leaked USB state can't be fixed in-process: exit so systemd
                    # restarts us clean (ExecStartPre also recovers the dongle).
                    log.exception("radio bring-up failed")
                    fatal(4, f"radio bring-up failed {failures} times ({e})")
                log.warning("radio bring-up failed (%s); retrying in %.0fs", e, BRINGUP_RETRY_DELAY)
                await asyncio.sleep(BRINGUP_RETRY_DELAY)
                continue
            link = self.link = LinkSupervisor(
                self.bus,
                radio,
                config=self.config,
                timing=self.timing,
                commands=self.commands,
                calls=self.calls,
                audio=self.sco,
            )
            channel = radio.rfcomm.listen(link.on_dlc)
            radio.add_sdp_record(lambda h, ch=channel: hfp.make_hf_sdp_records(h, ch, self.config))
            self.radio = radio
            link.start()
            log.info("hands-free ready on RFCOMM channel %s; pair your phone with it", channel)

    async def stop(self) -> None:
        await self.tasks.aclose()
        await self.pairing.aclose()
        if self.link is not None:
            await self.link.stop()

    def status(self) -> dict[str, Any]:
        conn = self.link.connection if self.link else None
        pipe = self.sco.pipe
        return {
            "adapter": "bumble-hfp",
            "radio": self.radio is not None,
            "radio_error": RadioProblem.of(self.ctx).as_dict(),
            "name": self.radio.peer_names.get(str(conn.peer_address), "")
            if self.radio and conn
            else "",
            "connected": self.commands.hf is not None,
            "peer": str(conn.peer_address) if conn else None,
            "call": self.calls.call.to_dict() if self.calls.call else None,
            "indicators": self.calls.indicators,
            "audio": pipe is not None,
            "audio_stats": {**pipe.live(), **self.sco.link_info} if pipe else None,
            "pairing": {
                **self.pairing.status(),
                "discoverable": bool(self.radio and self.radio.device.discoverable),
            },
        }

    def audio(self) -> AudioPipe | None:
        return self.sco.pipe

    # commands: raise PhoneCommandError when the phone doesn't do it --------
    async def answer(self) -> None:
        await self.commands.answer()

    async def hangup(self) -> None:
        await self.commands.hangup()

    async def dial(self, number: str) -> None:
        await self.commands.dial(number)

    async def send_dtmf(self, digits: str) -> None:
        await self.commands.send_dtmf(digits)

    async def set_volume(self, level: int) -> None:
        await self.commands.set_volume(level)

    async def assistant(self, *, on: bool) -> None:
        await self.commands.assistant(on=on)

    # pairing / device management ------------------------------------------
    async def phones(self) -> list[dict[str, Any]]:
        return await self.radio.bonded() if self.radio else []

    async def pair(self, seconds: float = 120) -> dict[str, Any]:
        """Open a discovery window so a phone can find and pair with us; after
        `seconds` the radio returns to its configured discoverability."""
        if self.radio is None:
            problem = RadioProblem.of(self.ctx).error
            raise RuntimeError(
                f"Bluetooth radio is not up, cannot pair ({problem or 'still starting'})"
            )
        until = await self.pairing.open(self.radio, seconds)
        return {"discoverable": True, "seconds": seconds, "until": until}

    async def forget(self, address: str) -> None:
        if self.radio:
            await self.radio.forget(address)
