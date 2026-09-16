"""Envelope serialization and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from olondunge.lanes.base import Envelope, JobId

STATUSES = frozenset({"ok", "ok_with_warnings", "blocked", "failed"})
VERDICTS = frozenset({"PASS", "FAIL", "BLOCKED"})
REPLY_CHAR_CAP = 4000
SUMMARY_WORD_CAP = 120


class EnvelopeError(ValueError):
    """An envelope dict failed validation. The message names the field."""


def cap_reply(text: str) -> str:
    return text if len(text) <= REPLY_CHAR_CAP else text[:REPLY_CHAR_CAP]


def cap_summary(text: str) -> str:
    return " ".join(text.split()[:SUMMARY_WORD_CAP])


def to_dict(envelope: Envelope) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": envelope.status,
        "job_id": envelope.job_id.value,
        "worker": envelope.worker,
        "kind": envelope.kind,
        "reply": envelope.reply,
        "summary": envelope.summary,
        "defects": list(envelope.defects),
        "evidence_refs": list(envelope.evidence_refs),
        "artifact_paths": list(envelope.artifact_paths),
        "duration_s": round(float(envelope.duration_s), 3),
        "model": envelope.model,
        "effort": envelope.effort,
    }
    if envelope.kind == "verify":
        data["verdict"] = envelope.verdict
    if envelope.usage is not None:
        data["usage"] = envelope.usage
    return data


def validate(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise EnvelopeError("envelope must be an object")
    for key in ("status", "job_id", "worker", "reply", "summary"):
        if not isinstance(data.get(key), str):
            raise EnvelopeError(f"invalid field {key}: must be a string")
    if data["status"] not in STATUSES:
        raise EnvelopeError(f"invalid field status: {data['status']!r}")
    for key in ("defects", "evidence_refs", "artifact_paths"):
        value = data.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise EnvelopeError(f"invalid field {key}: must be an array of strings")
    verdict = data.get("verdict")
    if verdict is not None and verdict not in VERDICTS:
        raise EnvelopeError(f"invalid field verdict: {verdict!r}")
    if len(data["summary"].split()) > SUMMARY_WORD_CAP:
        raise EnvelopeError(f"field summary exceeds {SUMMARY_WORD_CAP} words")
    if len(data["reply"]) > REPLY_CHAR_CAP:
        raise EnvelopeError(f"field reply exceeds {REPLY_CHAR_CAP} characters")


def from_dict(data: dict[str, Any], job_dir: Path) -> Envelope:
    validate(data)
    worker = data["worker"]
    usage = data.get("usage")
    try:
        duration = float(data.get("duration_s", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    return Envelope(
        status=data["status"],
        worker=worker,
        job_id=JobId(value=data["job_id"], job_dir=job_dir, worker=worker),
        reply=data["reply"],
        summary=data["summary"],
        defects=list(data["defects"]),
        evidence_refs=list(data["evidence_refs"]),
        artifact_paths=list(data["artifact_paths"]),
        usage=dict(usage) if isinstance(usage, dict) else None,
        duration_s=duration,
        kind=str(data.get("kind") or "work"),
        verdict=data.get("verdict"),
        model=data.get("model") if isinstance(data.get("model"), str) else None,
        effort=data.get("effort") if isinstance(data.get("effort"), str) else None,
    )
