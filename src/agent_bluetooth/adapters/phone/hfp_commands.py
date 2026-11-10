"""AT command layer of the hands-free kit: what we send the phone (ATA, AT+CHUP,
ATD, AT+VTS, AT+VGS) and the polls (AT+CIND?, AT+CLCC), each with a real budget.

Bumble's HfProtocol.execute_command waits only 1 s per reply unless told
otherwise, and its helpers (answer_incoming_call, terminate_call,
query_current_calls) never pass a timeout. An outer wait_for with a longer
budget is therefore dead code: the 1 s inner timeout fires first. So every
command here goes through execute_command with the budget passed down.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bumble import hfp
from bumble.core import ProtocolError

from ...core.errors import PhoneCommandError

if TYPE_CHECKING:
    from collections.abc import Awaitable

log = logging.getLogger(__name__)
CMD_TIMEOUT = 8.0  # s budget for one HFP command
DIAL_TIMEOUT = 12.0  # s: ATD returns only once the AG accepts the call
CIND_TIMEOUT = 4.0  # AT+CIND? heartbeat; a miss counts toward a wedged-controller restart
CLCC_TIMEOUT = 3.0  # AT+CLCC is best effort (caller number only)
VOLUME_MAX = 15  # HFP speaker gain scale is 0..15
CLCC_NUMBER = 5  # +CLCC: idx,dir,stat,mode,mpty[,number,type]


def _as_int(x: object) -> int:
    if isinstance(x, (bytes, bytearray)):
        x = x.decode()
    if isinstance(x, (str, int)):
        return int(x)
    raise TypeError(f"not an indicator value: {x!r}")


class HfpCommands:
    """Holds the live HFP protocol (None while no phone is linked)."""

    def __init__(self) -> None:
        self.hf: hfp.HfProtocol | None = None

    def _hf(self) -> hfp.HfProtocol:
        if self.hf is None:
            raise RuntimeError("phone not connected")
        return self.hf

    # commands: any failure raises PhoneCommandError ----------------------
    # Every command is time-boxed: a flaky controller must never hang a caller
    # (API handler, automation, router). A failure is raised to the caller; the
    # reconcile loop and reconnect bring state back to reality either way.
    async def _send(self, what: str, at: str, budget: float = CMD_TIMEOUT) -> None:
        hf = self._hf()
        await self._run(what, hf.execute_command(at, budget), budget)

    @staticmethod
    async def _run(what: str, coro: Awaitable[object], budget: float) -> None:
        try:
            await asyncio.wait_for(coro, budget)
        except TimeoutError as e:
            log.warning("HFP command %s timed out after %.0fs", what, budget)
            raise PhoneCommandError(what, "timeout", f"no reply within {budget:g}s") from e
        except ProtocolError as e:  # the AG answered ERROR / +CME ERROR / NO CARRIER ...
            log.warning("HFP command %s rejected: %s", what, e.error_name)
            raise PhoneCommandError(what, "rejected", f"phone replied {e.error_name}") from e
        except Exception as e:  # DLC closed / link torn down mid-command
            log.warning("HFP command %s failed", what, exc_info=True)
            raise PhoneCommandError(what, "link", str(e) or type(e).__name__) from e

    async def answer(self) -> None:
        log.info("answering call (ATA)")
        await self._send("answer", "ATA")
        log.info("ATA accepted")

    async def hangup(self) -> None:
        # Rejecting a ringing call and ending an active one are the same AT+CHUP
        # (HFP 4.14.1 / 4.15.1); Bumble's two helpers differ only in name.
        await self._send("hangup", "AT+CHUP")

    async def dial(self, number: str) -> None:
        # ATD only returns OK once the AG accepts the request; time-box it so a
        # number that never connects can't wedge the command path.
        await self._send("dial", f"ATD{number};", DIAL_TIMEOUT)

    async def send_dtmf(self, digits: str) -> None:
        for d in digits:
            await self._send("dtmf", f"AT+VTS={d}")

    async def set_volume(self, level: int) -> None:
        await self._send("volume", f"AT+VGS={max(0, min(VOLUME_MAX, level))}")

    async def assistant(self, *, on: bool) -> None:
        # The headset's assistant button (HFP 4.25): the phone wakes Siri /
        # Google Assistant and opens the audio link to us, with no call.
        await self._send("assistant", f"AT+BVRA={int(on)}")

    # polls: best effort, None on any failure -----------------------------
    async def read_indicators(self) -> list[int] | None:
        """AT+CIND? values in hf.ag_indicators order; None = missed heartbeat."""
        hf = self.hf
        if hf is None:
            return None
        try:
            resp = await asyncio.wait_for(
                hf.execute_command(
                    "AT+CIND?", CIND_TIMEOUT, response_type=hfp.AtResponseType.SINGLE
                ),
                timeout=CIND_TIMEOUT,
            )
        except Exception:  # any failure is a missed heartbeat; the loop must survive
            log.debug("AT+CIND? failed", exc_info=True)
            return None
        try:
            return [_as_int(x) for x in resp.parameters]
        except (TypeError, ValueError):
            return None

    async def current_number(self) -> str:
        """First number the phone lists in AT+CLCC, or "" (this hardware often
        never answers CLCC; callers keep whatever +CLIP gave them)."""
        hf = self.hf
        if hf is None:
            return ""
        try:
            responses = await asyncio.wait_for(
                hf.execute_command(
                    "AT+CLCC", CLCC_TIMEOUT, response_type=hfp.AtResponseType.MULTIPLE
                ),
                timeout=CLCC_TIMEOUT,
            )
        except Exception:  # best effort by design: keep the +CLIP number
            log.debug("AT+CLCC failed", exc_info=True)
            return ""
        for r in responses:
            if len(r.parameters) > CLCC_NUMBER and (number := r.parameters[CLCC_NUMBER]):
                return number.decode() if isinstance(number, bytes) else str(number)
        return ""
