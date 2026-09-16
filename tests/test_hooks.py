from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from olondunge import hooks

ROOT = Path(__file__).resolve().parents[1]


def _payload(prompt: str, event: str | None = "UserPromptSubmit") -> str:
    data: dict[str, object] = {"prompt": prompt, "session_id": "s"}
    if event is not None:
        data["hook_event_name"] = event
    return json.dumps(data)


def test_claude_hook_compiles_only_triggers() -> None:
    out = hooks.claude_output(_payload("tri: build a widget"))
    assert out.startswith("<tristack-compiled-contract>") and "Contract: 1.1-cc." in out
    assert hooks.claude_output(_payload("just a question")) == ""
    assert hooks.claude_output(_payload("tri: x", event="PreToolUse")) == ""
    assert hooks.claude_output(_payload("tri: x", event=None)) != ""
    assert hooks.claude_output("not json") == ""


def test_codex_hook_envelope_requires_event() -> None:
    out = hooks.codex_output(_payload("tri plan: design it"))
    data = json.loads(out)
    assert data["continue"] is True
    context = data["hookSpecificOutput"]["additionalContext"]
    assert "Contract: 1.1-cx." in context and "PLAN_ONLY" in context
    assert hooks.codex_output(_payload("tri: x", event=None)) == ""


def test_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRISTACK_FORGE_HOOK", "0")
    assert hooks.claude_output(_payload("tri: x")) == ""
    assert hooks.codex_output(_payload("tri: x")) == ""


def test_run_hook_never_raises() -> None:
    sink = io.StringIO()
    assert hooks.run_hook("claude", io.StringIO("{broken"), sink) == 0
    assert sink.getvalue() == ""


def test_plugin_hook_script_emits_full_contract_through_a_pipe() -> None:
    # The contract is ~55 KB; a hook that exits early truncates at the 8192 byte pipe buffer.
    proc = subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "tri_hook.py"), "claude"],
        input=_payload("tri: build a widget"),
        capture_output=True,
        text=True,
        check=True,
    )
    assert len(proc.stdout.encode()) > 40_000
    assert proc.stdout.rstrip().endswith("`Next authorized action:`.")


def test_tri_grok_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert hooks.grok_main(["--dry-run", "tri plan: migrate the tables"]) == 0
    out = capsys.readouterr().out
    assert "Contract: 1.1-gk." in out and "PLAN_ONLY" in out
    assert 'The user ran tri-grok with: "tri plan: migrate the tables"' in out


@pytest.mark.parametrize(
    "words", [["hello", "world,", "what", "time", "is", "it"], ["plan", "the", "migration"]]
)
def test_tri_grok_passes_non_trigger_text_through(
    capsys: pytest.CaptureFixture[str], words: list[str]
) -> None:
    assert hooks.grok_main(["--dry-run", *words]) == 0
    assert capsys.readouterr().out == " ".join(words)


def test_tri_grok_refuses_an_empty_trigger(capsys: pytest.CaptureFixture[str]) -> None:
    assert hooks.grok_main(["--dry-run", "tri:"]) == 1
    assert capsys.readouterr().out == ""


def test_tri_grok_execs_grok_with_one_argv_element(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "grok"
    record = tmp_path / "argv.json"
    fake.write_text(
        f"#!{sys.executable}\nimport json, sys\njson.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("TRI_GROK_BIN", str(fake))
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from olondunge.hooks import grok_main; grok_main(['--headless', 'tri: build it'])",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    argv = json.loads(record.read_text())
    assert argv[:2] == ["--always-approve", "-p"] and len(argv) == 3
    assert "Contract: 1.1-gk." in argv[2]
    plain = subprocess.run(
        [sys.executable, "-c", "from olondunge.hooks import grok_main; grok_main(['hello grok'])"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert plain.returncode == 0, plain.stderr
    assert json.loads(record.read_text()) == ["hello grok"]


def test_plugin_hook_entry_is_silent_under_grok(tmp_path: Path) -> None:
    entry = Path(__file__).resolve().parents[1] / "hooks" / "tri_hook.py"
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "tri plan: a widget"})
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    claude = subprocess.run(
        [sys.executable, str(entry), "claude"],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert claude.returncode == 0 and "<tristack-compiled-contract>" in claude.stdout
    grok = subprocess.run(
        [sys.executable, str(entry), "claude"],
        input=payload,
        capture_output=True,
        text=True,
        env={**env, "GROK_PLUGIN_ROOT": str(tmp_path)},
        timeout=30,
        check=False,
    )
    assert grok.returncode == 0 and grok.stdout == ""


def test_module_tri_grok_accepts_leading_flags(capsys: pytest.CaptureFixture[str]) -> None:
    from olondunge.cli import main

    assert main(["tri-grok", "--dry-run", "tri local: fix the test"]) == 0
    assert "EXECUTE_LOCAL" in capsys.readouterr().out


def test_tri_output_survives_a_closed_pipe() -> None:
    script = (
        "import sys; from olondunge.cli import main; "
        "sys.exit(main(['tri', '--host', 'codex', 'tri plan: smoke']))"
    )
    proc = subprocess.run(
        f'{sys.executable} -c "{script}" | head -c 10 > /dev/null; echo ${{PIPESTATUS[0]}}',
        shell=True,
        executable="/bin/bash",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.stdout.strip() == "0", proc.stderr
    assert "Traceback" not in proc.stderr


def test_tri_grok_quotes_a_long_trigger_whole_and_passes_dash_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    long_trigger = "tri: " + "x" * 700
    assert hooks.grok_main(["--dry-run", long_trigger]) == 0
    assert f"The user ran tri-grok with: {json.dumps(long_trigger)}" in capsys.readouterr().out
    assert hooks.grok_main(["--dry-run", "--verbose is what I want explained"]) == 0
    assert capsys.readouterr().out == "--verbose is what I want explained"


def test_plugin_hook_path_parses_on_python_3_9() -> None:
    # hooks/hooks.json runs a bare `python3`, which on some systems is older than this
    # package's own floor, so every module the hook imports must parse there.
    import ast

    root = Path(__file__).resolve().parents[1]
    for relative in (
        "hooks/tri_hook.py",
        "src/olondunge/__init__.py",
        "src/olondunge/hooks.py",
        "src/olondunge/tristack/__init__.py",
        "src/olondunge/tristack/core.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        ast.parse(source, filename=relative, feature_version=(3, 9))
