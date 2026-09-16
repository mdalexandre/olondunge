"""The lane protocol and the records that cross it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Health:
    """Presence probe for one lane."""

    up: bool
    reason: str
    binary: str | None


@dataclass
class Brief:
    """Everything a lane needs to start one job.

    `permitted_tools` and `authority` come from the packet and decide what the worker may
    do. `write_allowed` is computed by the surface, never taken from the host directly.
    """

    prompt: str
    cwd: Path
    prompt_file: Path | None = None
    max_turns: int | None = None
    timeout_s: float | None = None
    model: str | None = None
    effort: str | None = None
    permitted_tools: tuple[str, ...] = ()
    write_allowed: bool = False
    kind: str = "work"
    read_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobId:
    value: str
    job_dir: Path
    worker: str


@dataclass
class Envelope:
    """The collected result of one job. The reply is capped; full transcripts stay on disk."""

    status: str
    worker: str
    job_id: JobId
    reply: str
    summary: str
    defects: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    artifact_paths: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    duration_s: float = 0.0
    kind: str = "work"
    verdict: str | None = None
    model: str | None = None
    effort: str | None = None


class Lane(Protocol):
    """One worker CLI or endpoint."""

    name: str

    def health(self) -> Health: ...

    def submit(self, brief: Brief) -> JobId: ...
