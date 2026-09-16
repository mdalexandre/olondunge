from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from olondunge import setup


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if argv[:3] == ["codex", "mcp", "add"] and not setup.codex_home().is_dir():
            return subprocess.CompletedProcess(list(argv), 1, "", "CODEX_HOME does not exist")
        code = 1 if argv[:3] == ["claude", "mcp", "get"] else 0
        return subprocess.CompletedProcess(list(argv), code, "", "")


@pytest.fixture
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_bin: Path) -> Path:
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    return home


def test_dry_run_writes_nothing(user_home: Path) -> None:
    recorder = Recorder()
    report = setup.run_setup("all", dry_run=True, force=False, runner=recorder)
    assert not report.failed
    assert recorder.calls == []
    assert not (user_home / ".claude").exists() and not (user_home / ".codex").exists()
    assert {s.status for s in report.steps} == {"planned"}


def test_setup_all_installs_and_is_idempotent(user_home: Path, hermetic: Path) -> None:
    settings = user_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": []}}), encoding="utf-8")
    recorder = Recorder()
    report = setup.run_setup("all", dry_run=False, force=False, runner=recorder)
    assert not report.failed, report.as_dict()
    server = setup.server_command()
    assert ["claude", "mcp", "add", "-s", "user", "olondunge", "--", *server] in recorder.calls
    assert ["codex", "mcp", "add", "olondunge", "--", *server] in recorder.calls
    assert ["codex", "features", "enable", "hooks"] in recorder.calls
    assert ["grok", "mcp", "add", "-s", "user", "olondunge", "--", *server] in recorder.calls
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    hook = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert hook.endswith("-m olondunge hook claude")
    codex_hooks = json.loads((user_home / ".codex" / "hooks.json").read_text())
    assert codex_hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"].endswith("hook codex")
    for root in (
        user_home / ".claude" / "skills",
        user_home / ".codex" / "skills",
        user_home / ".grok" / "skills",
    ):
        assert (root / "olondunge" / "SKILL.md").is_file()
        assert (root / "tristack-compiler" / "SKILL.md").is_file()
    backups = list((hermetic / "backups").rglob("claude-settings.json"))
    assert backups and json.loads(backups[0].read_text())["model"] == "opus"
    stamp = backups[0].parent.name
    assert re.fullmatch(r"\d{8}T\d{6}Z", stamp), stamp
    assert backups[0].parent.parent == hermetic / "backups"

    again = setup.run_setup("all", dry_run=False, force=False, runner=Recorder())
    statuses = {(s.host, s.action): s.status for s in again.steps}
    assert statuses[("claude", "tri hook")] == "skipped"
    assert statuses[("claude", "skill olondunge")] == "skipped"
    assert len(json.loads(settings.read_text())["hooks"]["UserPromptSubmit"]) == 1


def test_invalid_settings_left_untouched(user_home: Path) -> None:
    settings = user_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json", encoding="utf-8")
    report = setup.run_setup("claude", dry_run=False, force=False, runner=Recorder())
    assert report.failed
    assert settings.read_text() == "{not json"


def test_existing_tri_hook_is_not_doubled(user_home: Path) -> None:
    settings = user_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "node tristack_forge_inject.js"}]}
            ]
        }
    }
    settings.write_text(json.dumps(existing), encoding="utf-8")
    report = setup.run_setup("claude", dry_run=False, force=False, runner=Recorder())
    step = next(s for s in report.steps if s.action == "tri hook")
    assert step.status == "skipped" and "--force" in step.detail
    assert json.loads(settings.read_text()) == existing


def test_missing_host_binary_is_skipped(user_home: Path, empty_path: None) -> None:
    report = setup.run_setup("all", dry_run=False, force=False, runner=Recorder())
    assert [s.status for s in report.steps] == ["skipped", "skipped", "skipped"]


def test_install_source_comes_from_package_metadata() -> None:
    source = setup.install_source()
    assert source.startswith("git+https://github.com/") and source.endswith("/olondunge")


def test_server_registered_reads_each_host_cli(
    fake_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "olondunge")
    assert setup.server_registered("grok", ["grok", "mcp", "list"]) is True
    assert setup.server_registered("claude", ["claude", "mcp", "get", "olondunge"]) is True
    monkeypatch.setenv("FAKE_EXIT", "1")
    assert setup.server_registered("claude", ["claude", "mcp", "get", "olondunge"]) is False
    assert setup.server_registered("nosuchcli", ["nosuchcli", "mcp", "list"]) is None
