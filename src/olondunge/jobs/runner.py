"""Poll and collect jobs from disk, so results survive a server restart.

A job is finished when `exit_code.txt` exists: the wrap (or the local lane's thread)
writes it last. Poll reconciles on that file instead of trusting the status it wrote at
launch, which is how a job that finished long ago used to keep reporting `running` while
its wrap sat as a zombie.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from olondunge.jobs import envelope as env_mod
from olondunge.jobs import store
from olondunge.lanes import PARSERS, process
from olondunge.lanes.base import Envelope, JobId
from olondunge.redact import redact

GRACE_S = 10.0
MAX_WAIT_S = 600.0

_VERDICT_RE = re.compile(r"(?im)^[\s>*_`#-]*VERDICT\s*:\s*[*_`]*\s*(PASS|FAIL|BLOCKED)\b")
_VERDICT_STATUS = {"PASS": "ok", "FAIL": "failed", "BLOCKED": "blocked"}
_LOGIN_MARKERS = (
    "please log in",
    "not logged in",
    "login required",
    "authentication required",
    "not authenticated",
    "please run claude login",
    "please run `claude login`",
    "not signed in",
    "sign in required",
    "no credentials found",
)
_LIMIT_MARKERS = ("usage limit", "rate limit exceeded", "quota exceeded")
_PAYMENT_RE = re.compile(r"\bHTTP(?:/[12](?:\.\d)?)?\s+402\b|usage balance exhausted", re.I)


def parse_verdict(reply: str) -> str | None:
    """The last `VERDICT: PASS|FAIL|BLOCKED` line in a reply, or None."""

    matches = _VERDICT_RE.findall(reply)
    return matches[-1].upper() if matches else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _exit_code(job_dir: Path) -> int | None:
    text = _read(job_dir / "exit_code.txt").strip()
    try:
        return int(text)
    except ValueError:
        return None


def assemble(job_dir: Path, job_doc: dict[str, Any], exit_code: int | None) -> Envelope:
    worker = str(job_doc.get("worker") or "unknown")
    kind = str(job_doc.get("kind") or "work")
    stdout = _read(job_dir / "stdout.txt")
    stderr = _read(job_dir / "stderr.txt")
    parser = PARSERS.get(worker)
    if parser is None:
        reply, usage, defects = stdout.strip(), None, [f"no parser for lane {worker}"]
    else:
        reply, usage, defects = parser(job_dir, stdout, stderr)
    defects = list(defects)
    status = "ok"
    # On a clean exit only stderr can report a login or limit problem: the reply is the
    # worker's own answer and may quote those phrases while succeeding.
    diagnostic = stderr if exit_code == 0 else f"{stdout}\n{stderr}"
    lowered = diagnostic.lower()
    # A clean exit with an answer is a success even if stderr logged a transient retry.
    suspicious = exit_code != 0 or not reply
    if job_doc.get("launch_error"):
        status = "blocked"
        defects.insert(0, str(job_doc["launch_error"]))
        reply = ""
    elif exit_code == 124 or (job_dir / "timed_out.txt").exists():
        status = "failed"
        defects.insert(0, f"timeout after {job_doc.get('timeout_s')}s")
    elif suspicious and _PAYMENT_RE.search(diagnostic):
        status = "blocked"
        defects.insert(0, f"{worker}: usage balance exhausted (HTTP 402)")
    elif suspicious and any(marker in lowered for marker in _LOGIN_MARKERS):
        status = "blocked"
        defects.insert(0, f"{worker}: not logged in; log in to the {worker} CLI and retry")
    elif suspicious and any(marker in lowered for marker in _LIMIT_MARKERS):
        status = "blocked"
        defects.insert(0, f"{worker}: usage or rate limit reached")
    elif exit_code not in (0, None):
        status = "failed"
        tail = " ".join(stderr.strip().splitlines()[-3:])[:300]
        defects.insert(0, f"exit code {exit_code}" + (f": {tail}" if tail else ""))
    elif defects:
        status = "failed" if not reply else "ok_with_warnings"

    redacted = redact(reply)
    if redacted != reply:
        defects.append("credential shaped text was redacted from the reply")
        if status == "ok":
            status = "ok_with_warnings"
    reply = env_mod.cap_reply(redacted)
    (job_dir / "reply.txt").write_text(redacted, encoding="utf-8")

    verdict: str | None = None
    if kind == "verify" and status in ("ok", "ok_with_warnings"):
        verdict = parse_verdict(redacted)
        if verdict is None:
            status = "failed"
            defects.append("the verifier reply carried no VERDICT: PASS, FAIL or BLOCKED line")
        else:
            status = _VERDICT_STATUS[verdict] if verdict != "PASS" else status

    summary_source = reply or (defects[0] if defects else f"{worker} finished: {status}")
    started = store.epoch_of(str(job_doc.get("started_at") or store.utc_now()))
    try:
        finished = (job_dir / "exit_code.txt").stat().st_mtime
    except OSError:
        finished = time.time()
    refs = [name for name in ("reply.txt", "stdout.txt", "stderr.txt") if (job_dir / name).exists()]
    return Envelope(
        status=status,
        worker=worker,
        job_id=JobId(value=job_dir.name, job_dir=job_dir, worker=worker),
        reply=reply,
        summary=env_mod.cap_summary(redact(summary_source)),
        defects=[redact(d) for d in defects],
        evidence_refs=refs,
        usage=usage,
        duration_s=max(finished - started, 0.001),
        kind=kind,
        verdict=verdict,
        model=job_doc.get("model") if isinstance(job_doc.get("model"), str) else None,
        effort=job_doc.get("effort") if isinstance(job_doc.get("effort"), str) else None,
    )


def _finalize(job_dir: Path, job_doc: dict[str, Any]) -> Envelope:
    stored = store.load_envelope(job_dir)
    if stored is not None:
        return store.complete_job(job_dir, stored)
    envelope = assemble(job_dir, job_doc, _exit_code(job_dir))
    return store.complete_job(job_dir, envelope)


def refresh(job_dir: Path) -> dict[str, Any]:
    """Bring job.json up to date with what is on disk. Returns the current job doc."""

    doc = store.load_job(job_dir)
    if doc.get("status") in store.TERMINAL:
        return doc
    job_id = job_dir.name
    pid = doc.get("pid") if isinstance(doc.get("pid"), int) else None
    finished = (job_dir / "exit_code.txt").exists()
    if finished:
        process.reap(job_id, pid)
        _finalize(job_dir, doc)
        return store.load_job(job_dir)
    wrap_gone = not doc.get("in_process") and process.reap(job_id, pid)
    if wrap_gone and (job_dir / "exit_code.txt").exists():
        # The wrap finished between the two checks above; its own exit code is the truth.
        _finalize(job_dir, doc)
        return store.load_job(job_dir)
    timeout_s = float(doc.get("timeout_s") or process.DEFAULT_TIMEOUT_S)
    overdue = time.time() > store.epoch_of(str(doc.get("started_at"))) + timeout_s + GRACE_S
    if wrap_gone or overdue:
        if overdue and not wrap_gone:
            process.kill_job(job_id, pid)
        reason = "the worker exceeded its timeout" if overdue else "the worker process vanished"
        with (job_dir / "stderr.txt").open("a", encoding="utf-8") as handle:
            handle.write(f"\n{reason}\n")
        (job_dir / "exit_code.txt").write_text("124" if overdue else "1", encoding="utf-8")
        if overdue:
            (job_dir / "timed_out.txt").write_text(str(timeout_s), encoding="utf-8")
        _finalize(job_dir, doc)
        return store.load_job(job_dir)
    return doc


def poll(job_id: str) -> dict[str, Any]:
    job_dir = store.job_dir_for(job_id)
    if job_dir is None or not (job_dir / "job.json").is_file():
        return {
            "status": "blocked",
            "job_id": job_id,
            "missing": f"unknown job {job_id}",
            "recovery": "use a job_id returned by alloc_dispatch, alloc_verify or alloc_call",
        }
    doc = refresh(job_dir)
    out: dict[str, Any] = {
        "status": "ok",
        "job_id": job_id,
        "job_status": doc.get("status"),
        "kind": doc.get("kind"),
        "lane": doc.get("lane"),
        "model": doc.get("model"),
        "effort": doc.get("effort"),
        "task_id": doc.get("task_id"),
        "started_at": doc.get("started_at"),
        "completed_at": doc.get("completed_at"),
        "timeout_s": doc.get("timeout_s"),
    }
    stored = store.load_envelope(job_dir)
    if stored is not None:
        out["envelope_status"] = stored.status
        if stored.kind == "verify":
            out["verdict"] = stored.verdict
    return out


def collect(job_id: str, wait_s: float = 0.0) -> dict[str, Any]:
    job_dir = store.job_dir_for(job_id)
    if job_dir is None or not (job_dir / "job.json").is_file():
        return {
            "status": "blocked",
            "job_id": job_id,
            "missing": f"unknown job {job_id}",
            "recovery": "use a job_id returned by alloc_dispatch, alloc_verify or alloc_call",
        }
    deadline = time.monotonic() + max(0.0, min(float(wait_s), MAX_WAIT_S))
    while True:
        doc = refresh(job_dir)
        if doc.get("status") in store.TERMINAL:
            stored = store.load_envelope(job_dir)
            if stored is None:
                return {
                    "status": "failed",
                    "job_id": job_id,
                    "error": "job is terminal but envelope.json is missing",
                }
            data = env_mod.to_dict(stored)
            data["job_dir"] = str(job_dir)
            return data
        if time.monotonic() >= deadline:
            return {
                "status": "blocked",
                "job_id": job_id,
                "job_status": doc.get("status"),
                "missing": "the job is still running",
                "recovery": "call alloc_poll until job_status is done, failed or blocked, "
                "then alloc_collect (or pass wait_s)",
            }
        time.sleep(0.5)
