"""Where Olondunge keeps its state. Every path is derived from OLONDUNGE_HOME."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    """OLONDUNGE_HOME when set, else ~/.olondunge. Read on every call, never cached."""

    raw = os.environ.get("OLONDUNGE_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".olondunge"


def jobs_root() -> Path:
    return home() / "jobs"


def scratch_root() -> Path:
    return home() / "scratch"


def ledger_path() -> Path:
    return home() / "ledger.jsonl"


def registry_override_path() -> Path:
    """A user registry replaces the packaged default when this file exists."""

    raw = os.environ.get("OLONDUNGE_REGISTRY", "").strip()
    if raw:
        return Path(raw).expanduser()
    return home() / "registry.json"


def is_inside(path: Path, root: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
        base = root.expanduser().resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents
