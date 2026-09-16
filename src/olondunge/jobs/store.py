"""Atomic job.json and envelope.json persistence."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from olondunge.jobs import envelope as env_mod
from olondunge.lanes.base import Envelope
from olondunge.paths import jobs_root

TERMINAL = frozenset({"done", "failed", "blocked"})
_ENVELOPE_TO_JOB = {
    "ok": "done",
    "ok_with_warnings": "done",
    "failed": "failed",
    "blocked": "blocked",
}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def epoch_of(stamp: str) -> float:
    text = stamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return datetime.now(UTC).timestamp()


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def create_job_dir() -> Path:
    root = jobs_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    job_dir = root / uuid.uuid4().hex
    job_dir.mkdir(mode=0o700)
    return job_dir


def job_dir_for(job_id: str) -> Path | None:
    """Resolve a job id to its directory, refusing anything that is not a plain id."""

    if not job_id or not all(ch in "0123456789abcdef" for ch in job_id) or len(job_id) != 32:
        return None
    return jobs_root() / job_id


def begin_job(job_dir: Path, **fields: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "job_id": job_dir.name,
        "status": "queued",
        "pid": None,
        "started_at": utc_now(),
        "job_dir": str(job_dir),
    }
    doc.update(fields)
    atomic_write_json(job_dir / "job.json", doc)
    return doc


def load_job(job_dir: Path) -> dict[str, Any]:
    data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"job.json is not an object: {job_dir}")
    return data


def update_job(job_dir: Path, **fields: Any) -> dict[str, Any]:
    doc = load_job(job_dir)
    doc.update(fields)
    atomic_write_json(job_dir / "job.json", doc)
    return doc


def load_envelope(job_dir: Path) -> Envelope | None:
    path = job_dir / "envelope.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return env_mod.from_dict(data, job_dir)
    except (OSError, ValueError):
        return None


def complete_job(job_dir: Path, envelope: Envelope) -> Envelope:
    """Persist the envelope, mark the job terminal, append the ledger row. Idempotent."""

    stored = load_envelope(job_dir)
    if stored is None:
        data = env_mod.to_dict(envelope)
        env_mod.validate(data)
        atomic_write_json(job_dir / "envelope.json", data)
        stored = envelope
    doc = update_job(
        job_dir,
        status=_ENVELOPE_TO_JOB.get(stored.status, "failed"),
        completed_at=utc_now(),
    )
    from olondunge.jobs.ledger import append_once

    append_once(doc, stored, job_dir)
    return stored
