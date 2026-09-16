"""Blind verification packets.

A verifier receives the artifact, the acceptance criteria and the paths, never the
producer's conclusion. The packet keeps every original input (a verifier that cannot see
the inputs cannot check the artifact against them), inlines the artifact text so a lane
that cannot open files still sees it, and demands a final `VERDICT:` line that collect
parses into the envelope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from olondunge.redact import redact

PRODUCER_CONCLUSION_MARKER = "PRODUCER_CONCLUSION"
FORBIDDEN_CANDIDATE_KEYS = frozenset({"verdict", "conclusion", "score", "confidence", "reasoning"})
READ_TOOLS = ("Read", "Glob", "Grep", "WebSearch")
PER_FILE_CHARS = 20_000
TOTAL_CHARS = 60_000
VERDICT_INSTRUCTION = (
    "Check each acceptance criterion against the artifact and the inputs, citing the "
    "evidence you observed for each. Do not modify any file. End your reply with exactly "
    "one line: VERDICT: PASS, VERDICT: FAIL, or VERDICT: BLOCKED (BLOCKED when you could "
    "not check a criterion)."
)


class LeakError(ValueError):
    """The candidate carries the producer's conclusion; a blind check would be anchored."""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def check_leak(packet: dict[str, Any], candidate: dict[str, Any]) -> None:
    for scope, doc in (("candidate", candidate), ("candidate.artifact", candidate.get("artifact"))):
        if isinstance(doc, dict):
            leaked = sorted(FORBIDDEN_CANDIDATE_KEYS & {str(k).lower() for k in doc})
            if leaked:
                raise LeakError(
                    f"{scope} carries {leaked}; a blind verifier receives the artifact, "
                    "criteria and paths only"
                )
    blob = json.dumps([packet, candidate], sort_keys=True, default=str)
    if PRODUCER_CONCLUSION_MARKER in blob:
        raise LeakError(f"the packet or candidate contains {PRODUCER_CONCLUSION_MARKER}")


def artifact_paths(candidate: dict[str, Any]) -> list[str]:
    paths = _strings(candidate.get("artifact_paths"))
    artifact = candidate.get("artifact")
    if isinstance(artifact, dict):
        paths += _strings(artifact.get("paths")) + _strings(artifact.get("path"))
    elif isinstance(artifact, str) and artifact.strip().startswith("/"):
        paths.append(artifact.strip())
    seen: list[str] = []
    for item in paths:
        if item not in seen:
            seen.append(item)
    return seen


def _artifact_text(candidate: dict[str, Any]) -> str | None:
    artifact = candidate.get("artifact")
    if isinstance(artifact, dict):
        for key in ("text", "content"):
            if isinstance(artifact.get(key), str) and artifact[key].strip():
                return str(artifact[key])
    if isinstance(artifact, str) and not artifact.strip().startswith("/"):
        return artifact
    return None


def _inline(paths: list[str], text: str | None) -> str:
    budget = TOTAL_CHARS
    blocks: list[str] = []
    if text:
        chunk = text[:PER_FILE_CHARS]
        blocks.append(f"### artifact text\n{chunk}")
        budget -= len(chunk)
    for raw in paths:
        if budget <= 0:
            blocks.append(f"### {raw}\n(not inlined: size budget reached; open the file)")
            continue
        path = Path(raw).expanduser()
        try:
            if path.is_dir():
                listing = sorted(p.name for p in path.iterdir())[:200]
                body = "directory listing:\n" + "\n".join(listing)
            else:
                body = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            body = f"(unreadable: {type(exc).__name__})"
        chunk = body[: min(PER_FILE_CHARS, budget)]
        if len(chunk) < len(body):
            chunk += "\n(truncated; open the file for the rest)"
        blocks.append(f"### {raw}\n{chunk}")
        budget -= len(chunk)
    return redact("\n\n".join(blocks))


def build_verify_packet(packet: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """The verifier's packet. Raises LeakError when the check could not be blind."""

    check_leak(packet, candidate)
    task_id = candidate.get("task_id") or packet.get("task_id") or "untitled"
    paths = artifact_paths(candidate)
    evidence = _strings(candidate.get("evidence"))
    inputs = _strings(packet.get("inputs"))
    merged_inputs = (
        inputs
        + [p for p in paths if p not in inputs]
        + [e for e in evidence if e not in inputs and e not in paths]
    )
    criteria = (
        _strings(packet.get("acceptance_criteria"))
        or _strings(packet.get("evidence_obligations"))
        or _strings(packet.get("expected_output"))
    )
    if not criteria:
        raise ValueError(
            "packet needs acceptance_criteria (or evidence_obligations, or expected_output) "
            "for a verifier to check against"
        )
    if not paths and _artifact_text(candidate) is None:
        raise ValueError("candidate needs artifact_paths or artifact.text to verify")
    objective = packet.get("objective") or packet.get("mission") or packet.get("question") or ""
    tools = [t for t in _strings(packet.get("permitted_tools")) if t in READ_TOOLS]
    prohibited = _strings(packet.get("prohibited_actions"))
    if "modifying any file" not in prohibited:
        prohibited.append("modifying any file")
    verify_packet: dict[str, Any] = {
        "task_id": f"{task_id}-verify",
        "objective": (
            f"Independently verify the artifact for task {task_id} against the acceptance "
            f"criteria. The original objective was: {objective}"
        ),
        "inputs": merged_inputs,
        "acceptance_criteria": criteria,
        "expected_output": VERDICT_INSTRUCTION,
        "permitted_tools": tools or ["Read", "Glob", "Grep"],
        "authority_ceiling": ["read-local-filesystem"],
        "prohibited_actions": prohibited,
        "stop_conditions": ["every criterion has a result", "a criterion cannot be checked"],
        "context": "Artifact under verification (inlined for lanes that cannot open files):\n\n"
        + _inline(paths, _artifact_text(candidate)),
        "capabilities": ["independent_verification"],
    }
    for key in ("timeout_s", "tool_call_limit", "workdir"):
        if key in packet:
            verify_packet[key] = packet[key]
    # The producer's model and effort belong to the producer's lane; the verifier has its own.
    for source, target in (("verifier_model", "model"), ("verifier_effort", "effort")):
        value = candidate.get(source, packet.get(source))
        if isinstance(value, str) and value.strip():
            verify_packet[target] = value.strip()
    if isinstance(packet.get("verifier_lane"), str):
        verify_packet["lane"] = packet["verifier_lane"]
    return verify_packet
