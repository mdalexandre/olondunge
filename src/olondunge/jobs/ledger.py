"""Append-only ledger.jsonl: one row per finished job."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from olondunge.lanes.base import Envelope
from olondunge.paths import ledger_path
from olondunge.redact import redact

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]


def _rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _tokens(usage: dict[str, Any] | None) -> int | None:
    if not isinstance(usage, dict):
        return None
    total = 0
    found = False
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            total += value
            found = True
    return total if found else None


def append_once(job_doc: dict[str, Any], envelope: Envelope, job_dir: Path) -> None:
    """Append a row unless one for this job_id exists; check and write share one lock."""

    row = {
        "job_id": envelope.job_id.value,
        "kind": envelope.kind,
        "lane": str(job_doc.get("lane") or envelope.worker),
        "model": envelope.model,
        "effort": envelope.effort,
        "task_id": job_doc.get("task_id"),
        "started_at": str(job_doc.get("started_at") or ""),
        "duration_s": round(float(envelope.duration_s), 3),
        "outcome": envelope.status,
        "verdict": envelope.verdict,
        "tokens": _tokens(envelope.usage),
        "cost_usd": (envelope.usage or {}).get("cost_usd"),
    }
    cleaned = {k: redact(v) if isinstance(v, str) else v for k, v in row.items()}
    line = json.dumps(cleaned, separators=(",", ":")) + "\n"
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        if any(existing.get("job_id") == row["job_id"] for existing in _rows(handle.read())):
            return
        handle.seek(0, os.SEEK_END)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def read_rows() -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.is_file():
        return []
    return _rows(path.read_text(encoding="utf-8"))
