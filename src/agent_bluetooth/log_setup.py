"""Daemon logging: one line per thing that happened, nothing on a timer.

The daemon runs for weeks under launchd/systemd, so INFO is for state changes a
person would want to read later (phone came/went, call, sync, errors). Polls,
per-packet chatter and telemetry stay at DEBUG and show up only with -v.
"""

from __future__ import annotations

import logging

# Library loggers and the quietest level each may log at.
_LEVELS = {
    # Bumble logs every HCI/SCO packet at DEBUG. With a live SCO (voice) link
    # that's thousands of lines/sec: it pegs the CPU and starves the event loop,
    # dropping the very call it's logging. INFO even under -v.
    "bumble.host": logging.INFO,
    "bumble.transport.usb": logging.INFO,
    "bumble.hci": logging.INFO,
    "bumble.rfcomm": logging.INFO,
    "bumble.l2cap": logging.INFO,
    # A multi-line feature dump on every reconnect and every signal-bar change;
    # our own link/call logs already say what matters.
    "bumble.hfp": logging.WARNING,
    # The LiveKit SDK's own connect/disconnect lines repeat our "-> room" and
    # "room closed".
    "livekit": logging.WARNING,
}
# One line per HTTP request, and the app polls /api/status every few seconds.
_QUIET_UNLESS_VERBOSE = ("aiohttp.access",)


class _NoStrayLibraryInfo(logging.Filter):
    """Bumble calls logging.info() on the ROOT logger in places (e.g. every
    unsolicited AT response it ignores). Keep its warnings, drop that INFO."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or "/bumble/" not in record.pathname


class _NoKnownFalseAlarms(logging.Filter):
    """LiveKit logs ERROR on every room join: its internal '_data_track'
    channel closes as the publisher renegotiates. Calls are fine; drop it."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "'_data_track' closed unexpectedly" not in record.getMessage()


def configure(instance: str, *, verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=f"%(asctime)s %(levelname).1s [{instance}] %(name)s: %(message)s",
    )
    # A logger's filters see only records created on it, which is exactly the
    # stray root-level calls; children's records reach handlers untouched.
    logging.getLogger().addFilter(_NoStrayLibraryInfo())
    logging.getLogger("livekit").addFilter(_NoKnownFalseAlarms())
    for name, level in _LEVELS.items():
        logging.getLogger(name).setLevel(level)
    if not verbose:
        for name in _QUIET_UNLESS_VERBOSE:
            logging.getLogger(name).setLevel(logging.WARNING)
