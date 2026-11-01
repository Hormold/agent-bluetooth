"""TOML config with ${ENV} / ${ENV:-default} expansion and an optional .env file."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

_VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _VAR.sub(lambda m: os.environ.get(m[1], m[2] or ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    load_dotenv(path.parent / ".env")
    with path.open("rb") as f:
        return _expand(tomllib.load(f))
