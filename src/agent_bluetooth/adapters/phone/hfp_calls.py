"""Call state of the hands-free kit, driven by +CIEV indicators, NOT CLCC.

The phone pushes call / callsetup indicators reliably; CLCC is only asked for
the number. A missed +CIEV is healed by the AT+CIND? heartbeat (see hfp_link).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bumble import hfp

from ...core.events import E
from ...core.ports import Call

if TYPE_CHECKING:
    from ...core.bus import EventBus
    from ...core.tasks import Tasks
    from .hfp_audio import ScoAudio
    from .hfp_commands import HfpCommands

log = logging.getLogger(__name__)
Ind = hfp.AgIndicator
_STATE_EVENT = {"ringing": E.CALL_RINGING, "dialing": E.CALL_DIALING, "active": E.CALL_ACTIVE}


class CallTracker:
    def __init__(self, bus: EventBus, commands: HfpCommands, audio: ScoAudio, tasks: Tasks) -> None:
        self.bus = bus
        self.tasks = tasks  # the phone's: CLCC lookups end with the phone
        self.commands = commands
        self.audio = audio
        self.call: Call | None = None
        self.indicators: dict[str, int] = {}

    def load(
        self, ag_indicators: list[hfp.AgIndicatorState], values: list[int] | None = None
    ) -> None:
        """Take the indicator snapshot (SLC setup, or an AT+CIND? reply) and
        re-derive the call from it."""
        for i, state in enumerate(ag_indicators):
            if values is None:
                self.indicators[state.indicator.value] = state.current_status
            elif i < len(values):
                self.indicators[state.indicator.value] = values[i]
        self.update()

    async def on_indicator(self, state: hfp.AgIndicatorState) -> None:
        name, value = state.indicator.value, state.current_status
        self.indicators[name] = value
        if state.indicator in (Ind.CALL, Ind.CALL_SETUP, Ind.CALL_HELD):
            self.update()
        else:
            self.bus.publish(E.PHONE_STATUS, **{name: value})

    def update(self) -> None:
        """Derive call state from the call / callsetup indicators."""
        call_ind = int(self.indicators.get("call", 0))
        setup = int(self.indicators.get("callsetup", 0))
        if call_ind:
            state = "active"
        elif setup == 1:
            state = "ringing"
        elif setup in (2, 3):
            state = "dialing"
        else:
            self.end()
            return
        if self.call is None:
            direction = "out" if setup in (2, 3) else "in"
            self.call = Call(direction=direction, number="", state="")
            self.tasks.spawn(self.fetch_number(), name="hfp-clcc")
        if state != self.call.state:
            self.call.state = state
            self.bus.publish(_STATE_EVENT[state], call=self.call.to_dict())
            if self.audio.pipe and state == "active":
                self.audio.announce(up=True)

    async def fetch_number(self) -> None:
        """Best-effort caller number via CLCC. Never blocks state; if it times
        out (common on this hardware) we just keep whatever +CLIP gave us."""
        if self.call is None or self.call.number:
            return
        number = await self.commands.current_number()
        if number and self.call is not None:
            self.call.number = number

    def on_clip(self, cli: hfp.CallLineIdentification) -> None:
        if self.call and not self.call.number:
            self.call.number = cli.number
            if cli.alpha:
                self.call.name = cli.alpha

    def end(self) -> None:
        if self.call:
            self.call.state = "ended"
            self.bus.publish(E.CALL_ENDED, call=self.call.to_dict())
            self.call = None
