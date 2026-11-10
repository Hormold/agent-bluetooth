#!/usr/bin/env bash
# The whole quality gate. CI runs exactly this; run it before every push.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --quiet ruff check src examples/livekit-agent/*.py
uv run --quiet ruff format --check src examples/livekit-agent/*.py
uv run --quiet ty check
uv run --quiet deptry src   # every import is a declared dependency
uv run --quiet lint-imports --no-cache   # architecture contracts, .importlinter
uv run --quiet python scripts/max_lines.py
bash -n deploy/recover-usb.sh
scripts/smoke.sh
