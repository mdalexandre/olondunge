"""Hermetic fixtures: a private OLONDUNGE_HOME, no real Codex home, and fake agent CLIs."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

FAKE_CLI = r'''#!{python}
"""Fake agent CLI for tests. Behaviour is driven by FAKE_* environment variables."""
import json, os, sys, time
name = os.path.basename(sys.argv[0])
log_dir = os.environ.get("FAKE_LOG_DIR")
if log_dir:
    with open(os.path.join(log_dir, f"{{name}}-{{os.getpid()}}.json"), "w") as fh:
        json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "depth": os.environ.get("OLONDUNGE_DEPTH"), "coordinator": os.environ.get("CLAUDE_CODE_COORDINATOR_MODE")}}, fh)
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
reply = os.environ.get("FAKE_REPLY", "FAKE REPLY OK")
sys.stderr.write(os.environ.get("FAKE_STDERR", ""))
code = int(os.environ.get("FAKE_EXIT", "0"))
if name == "claude":
    print(json.dumps({{"type": "result", "result": reply, "is_error": False,
                      "usage": {{"input_tokens": 11, "output_tokens": 7}}, "total_cost_usd": 0.001}}))
elif name == "codex":
    args = sys.argv[1:]
    if "--output-last-message" in args:
        with open(args[args.index("--output-last-message") + 1], "w") as fh:
            fh.write(reply)
    print("codex transcript noise")
else:
    print(reply)
sys.exit(code)
'''


@pytest.fixture(autouse=True)
def hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "olondunge-home"
    monkeypatch.setenv("OLONDUNGE_HOME", str(home))
    monkeypatch.setenv("OLONDUNGE_REGISTRY", str(tmp_path / "no-registry.json"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("OLONDUNGE_LOCAL_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("TRISTACK_FORGE_HOOK", raising=False)
    monkeypatch.delenv("OLONDUNGE_TRI_HOOK", raising=False)
    monkeypatch.delenv("OLONDUNGE_DEPTH", raising=False)
    monkeypatch.delenv("OLONDUNGE_MAX_DEPTH", raising=False)
    return home


@pytest.fixture
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "fake-log"
    bin_dir.mkdir()
    log_dir.mkdir()
    for name in ("claude", "codex", "grok"):
        path = bin_dir / name
        path.write_text(FAKE_CLI.format(python=sys.executable), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_LOG_DIR", str(log_dir))
    return log_dir


@pytest.fixture
def empty_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
