"""Fail when a source file outgrows MAX_LINES: split it by responsibility."""

from __future__ import annotations

import sys
from pathlib import Path

MAX_LINES = 500

too_long = [
    f"{path}: {count} lines (max {MAX_LINES})"
    for path in sorted(Path("src").rglob("*.py"))
    if (count := len(path.read_text().splitlines())) > MAX_LINES
]
sys.stdout.write("".join(f"{line}\n" for line in too_long))
sys.exit(1 if too_long else 0)
