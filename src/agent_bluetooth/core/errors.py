"""Errors that cross the port boundary: adapters raise them, the API maps them."""

from __future__ import annotations

from typing import Literal

# timeout: the phone never replied in time; rejected: it replied with an error
# (ERROR, +CME ERROR, NO CARRIER, BUSY...); link: the channel broke mid-command.
Reason = Literal["timeout", "rejected", "link"]


class PhoneCommandError(RuntimeError):
    """A command reached the phone (or tried to) and did not succeed. Distinct
    from a plain RuntimeError ("phone not connected"): the request was valid
    and the phone was there, it just didn't do it."""

    def __init__(self, command: str, reason: Reason, detail: str) -> None:
        super().__init__(f"phone {command} failed ({reason}): {detail}")
        self.command = command
        self.reason: Reason = reason
        self.detail = detail

    def as_dict(self) -> dict[str, str]:
        return {"error": str(self), "command": self.command, "reason": self.reason}
