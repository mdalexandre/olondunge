"""Lifecycle through fake CLIs: dispatch, poll, collect, verify. Covers defects 06a to 06d."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from olondunge.jobs import store
from olondunge.surface import Surface


def _packet(**extra: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "task_id": "t1",
        "objective": "Summarize the input file.",
        "expected_output": "a short summary",
        "acceptance_criteria": ["the summary names the file"],
        "permitted_tools": ["Read"],
        "authority_ceiling": ["read-local-filesystem"],
    }
    packet.update(extra)
    return packet


def _argv_of(log_dir: Path, name: str) -> list[str]:
    logs = sorted(log_dir.glob(f"{name}-*.json"), key=lambda p: p.stat().st_mtime)
    assert logs, f"{name} was never started"
    return list(json.loads(logs[-1].read_text())["argv"])


@pytest.mark.parametrize("lane", ["claude", "codex", "grok"])
def test_dispatch_collect_returns_reply_and_terminal_job(fake_bin: Path, lane: str) -> None:
    surface = Surface()
    started = surface.dispatch(_packet(lane=lane, effort="high"))
    assert started["status"] == "ok", started
    job_id = started["job_id"]
    envelope = surface.collect(job_id, wait_s=30)
    assert envelope["status"] == "ok", envelope
    # Defect 06c: the collected envelope carries the reply.
    assert envelope["reply"] == "FAKE REPLY OK"
    assert envelope["effort"] == "high"
    job_dir = store.job_dir_for(job_id)
    assert job_dir is not None
    # Defect 06d: job.json is terminal, not stuck at running.
    assert store.load_job(job_dir)["status"] == "done"
    assert "high" in " ".join(_argv_of(fake_bin, lane))
    ledger = surface.ledger_text()
    assert job_id in ledger and '"effort": "high"' in ledger


def test_collect_is_non_blocking_while_running(
    fake_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SLEEP", "3")
    surface = Surface()
    job_id = surface.dispatch(_packet(lane="grok"))["job_id"]
    early = surface.collect(job_id)
    assert early["status"] == "blocked" and early["job_status"] == "running"
    polled = surface.poll(job_id)
    assert polled["job_status"] in {"running", "done"}
    assert surface.collect(job_id, wait_s=30)["status"] == "ok"


def test_timeout_fails_the_job(fake_bin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SLEEP", "30")
    surface = Surface()
    job_id = surface.dispatch(_packet(lane="grok", timeout_s=1))["job_id"]
    envelope = surface.collect(job_id, wait_s=40)
    assert envelope["status"] == "failed"
    assert any("timeout" in d for d in envelope["defects"])


def test_nonzero_exit_and_login_marker(fake_bin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EXIT", "1")
    monkeypatch.setenv("FAKE_REPLY", "")
    monkeypatch.setenv("FAKE_STDERR", "Error: not logged in")
    surface = Surface()
    job_id = surface.dispatch(_packet(lane="grok"))["job_id"]
    envelope = surface.collect(job_id, wait_s=30)
    assert envelope["status"] == "blocked"
    assert "log in" in envelope["defects"][0]


def test_clean_exit_with_login_words_in_reply_stays_ok(
    fake_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "The page says: please log in to continue.")
    surface = Surface()
    job_id = surface.dispatch(_packet(lane="grok"))["job_id"]
    assert surface.collect(job_id, wait_s=30)["status"] == "ok"


def test_missing_binary_is_blocked_not_hung(empty_path: None) -> None:
    surface = Surface()
    plan = surface.plan(_packet(lane="grok"))
    assert plan["status"] == "blocked"
    assert "not found" in plan["dropped"]["grok"]


def _candidate(tmp_path: Path, **extra: object) -> dict[str, object]:
    artifact = tmp_path / "artifact.md"
    artifact.write_text("# Summary of input.txt\n", encoding="utf-8")
    candidate: dict[str, object] = {"artifact_paths": [str(artifact)], "evidence": ["ran wc"]}
    candidate.update(extra)
    return candidate


def test_verify_keeps_packet_inputs_and_maps_blocked_verdict(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("hello\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_REPLY", "criterion 1: could not open\nVERDICT: BLOCKED")
    surface = Surface()
    packet = _packet(inputs=[str(source)])
    started = surface.verify(packet, _candidate(tmp_path, producer="grok"))
    assert started["status"] == "ok", started
    assert started["verifier"] != "grok"
    envelope = surface.collect(started["job_id"], wait_s=30)
    # Defect 06b: a BLOCKED verdict is never reported as ok.
    assert envelope["status"] == "blocked"
    assert envelope["verdict"] == "BLOCKED"
    lane = started["verifier"]
    argv = " ".join(_argv_of(fake_bin, lane))
    prompt = argv
    if lane == "grok":
        prompt_file = Path(_argv_of(fake_bin, "grok")[1])
        prompt = prompt_file.read_text(encoding="utf-8")
    # Defect 06a: the verifier sees the original inputs as well as the artifact.
    assert str(source) in prompt
    assert "Summary of input.txt" in prompt


def test_verify_pass_and_missing_verdict(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    surface = Surface()
    monkeypatch.setenv("FAKE_REPLY", "all good\n**VERDICT: PASS**")
    job = surface.verify(_packet(), _candidate(tmp_path))["job_id"]
    passed = surface.collect(job, wait_s=30)
    assert (passed["status"], passed["verdict"]) == ("ok", "PASS")
    monkeypatch.setenv("FAKE_REPLY", "looks fine to me")
    job = surface.verify(_packet(), _candidate(tmp_path))["job_id"]
    missing = surface.collect(job, wait_s=30)
    assert missing["status"] == "failed" and missing["verdict"] is None


def test_verify_refuses_a_leaked_conclusion(tmp_path: Path) -> None:
    result = Surface().verify(_packet(), _candidate(tmp_path, verdict="PASS"))
    assert result["status"] == "blocked"
    assert "blind" in result["recovery"] or "verdict" in result["reason"]


def test_workdir_write_requires_authority(fake_bin: Path, tmp_path: Path) -> None:
    surface = Surface()
    read_only = surface.dispatch(
        _packet(lane="codex", workdir=str(tmp_path), permitted_tools=["Write"])
    )
    assert read_only["write_allowed"] is False
    writable = surface.dispatch(
        _packet(
            lane="codex",
            workdir=str(tmp_path),
            permitted_tools=["Write"],
            authority_ceiling=["write-workdir"],
        )
    )
    assert writable["write_allowed"] is True
    for started in (read_only, writable):
        surface.collect(started["job_id"], wait_s=30)
    time.sleep(0.1)


def test_workers_carry_depth_and_nested_servers_refuse(
    fake_bin: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    surface = Surface()
    job_id = surface.dispatch(_packet(lane="grok"))["job_id"]
    surface.collect(job_id, wait_s=30)
    logs = sorted(fake_bin.glob("grok-*.json"))
    assert json.loads(logs[-1].read_text())["depth"] == "1"
    monkeypatch.setenv("OLONDUNGE_DEPTH", "1")
    refused = surface.dispatch(_packet(lane="grok"))
    assert refused["status"] == "blocked" and "depth" in refused["missing"]
    assert surface.verify(_packet(), _candidate(tmp_path))["status"] == "blocked"
    assert surface.plan(_packet(lane="grok"))["status"] == "ok"


def test_workers_do_not_inherit_the_host_session(
    fake_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from olondunge.lanes import process

    for key in ("CLAUDECODE", "CLAUDE_CODE_COORDINATOR_MODE", "CLAUDE_CODE_MESSAGING_TOKEN"):
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    scrubbed = process.scrub_host_session_env(dict(os.environ))
    assert "CLAUDE_CODE_COORDINATOR_MODE" not in scrubbed and "CLAUDECODE" not in scrubbed
    assert "CLAUDE_CODE_MESSAGING_TOKEN" not in scrubbed
    assert scrubbed["CLAUDE_CODE_USE_BEDROCK"] == "1"
    surface = Surface()
    for lane in ("claude", "grok"):
        job_id = surface.dispatch(_packet(lane=lane))["job_id"]
        surface.collect(job_id, wait_s=30)
        log = json.loads(sorted(fake_bin.glob(f"{lane}-*.json"))[-1].read_text())
        assert log["coordinator"] is None, lane


def test_poll_finishes_and_reaps_a_job_nobody_collected(fake_bin: Path) -> None:
    # Defect 06d: alloc_poll must read the wrap's exit code, mark the job done and reap it.
    from olondunge.lanes import process

    surface = Surface()
    job_id = surface.dispatch(_packet(lane="grok"))["job_id"]
    job_dir = store.job_dir_for(job_id)
    assert job_dir is not None
    deadline = time.time() + 30
    while not (job_dir / "exit_code.txt").exists() and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    assert store.load_job(job_dir)["status"] == "running"
    assert job_id in process._CHILDREN
    polled = surface.poll(job_id)
    assert polled["job_status"] == "done", polled
    assert store.load_job(job_dir)["status"] == "done"
    assert job_id not in process._CHILDREN


def test_collected_reply_is_capped_and_full_text_kept(
    fake_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Defect 06c: the envelope carries the reply, capped; reply.txt keeps all of it.
    from olondunge.jobs.envelope import REPLY_CHAR_CAP

    long_reply = "word " * 2000
    monkeypatch.setenv("FAKE_REPLY", long_reply)
    surface = Surface()
    job_id = surface.dispatch(_packet(lane="grok"))["job_id"]
    envelope = surface.collect(job_id, wait_s=30)
    assert len(envelope["reply"]) == REPLY_CHAR_CAP
    job_dir = store.job_dir_for(job_id)
    assert job_dir is not None
    assert (job_dir / "reply.txt").read_text().strip() == long_reply.strip()


@pytest.mark.parametrize(
    ("lane", "model", "effort", "pairs"),
    [
        ("claude", "opus", "max", [("--model", "opus"), ("--effort", "max")]),
        ("codex", "gpt-5.5", "high", [("--model", "gpt-5.5")]),
        ("grok", "grok-4", "low", [("--model", "grok-4"), ("--reasoning-effort", "low")]),
    ],
)
def test_packet_model_and_effort_reach_the_cli(
    fake_bin: Path, lane: str, model: str, effort: str, pairs: list[tuple[str, str]]
) -> None:
    surface = Surface()
    started = surface.dispatch(_packet(lane=lane, model=model, effort=effort))
    assert started["status"] in {"ok", "ok_with_warnings"}, started
    assert started["model"] == model and started["effort"] == effort
    surface.collect(started["job_id"], wait_s=30)
    argv = _argv_of(fake_bin, lane)
    for flag, value in pairs:
        assert flag in argv and argv[argv.index(flag) + 1] == value, (flag, argv)
    if lane == "codex":
        assert f'model_reasoning_effort="{effort}"' in argv


class _Chunks:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    def read1(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


def test_transcript_pump_is_linear_and_redacts_across_chunks(tmp_path: Path) -> None:
    from olondunge.jobs import wrap

    line = b"worker progress line with nothing secret in it at all, just words\n"
    body = line * 32000  # about 2 MB
    chunks = [body[i : i + 4096] for i in range(0, len(body), 4096)]
    key = (
        b"-----BEGIN PRIVATE KEY-----\n"
        + b"QUFBQUFBQUFBQUFB\n" * 40
        + b"-----END PRIVATE KEY-----\n"
    )
    token = b"api_key=Zq8vN3pLx7Rt2kWm9Yb4 done\n"
    tail = key + token + "caf\u00e9".encode()
    chunks += [tail[i : i + 7] for i in range(0, len(tail), 7)]
    dest = tmp_path / "stdout.txt"
    started = time.perf_counter()
    wrap._pump(_Chunks(chunks), dest)  # type: ignore[arg-type]
    assert time.perf_counter() - started < 5.0
    text = dest.read_text(encoding="utf-8")
    assert text.startswith(line.decode()) and text.count("worker progress") == 32000
    assert "QUFBQUFBQUFBQUFB" not in text and "Zq8vN3pLx7Rt2kWm9Yb4" not in text
    assert "[REDACTED]" in text and text.endswith("caf\u00e9")
