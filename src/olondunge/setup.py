"""`olondunge setup claude|codex|grok|all`: register the MCP server, install the skills, and
wire `tri:` activation for each host the user has installed.

Every config file is backed up under ~/.olondunge/backups/<timestamp>/ before it changes, a
file that does not parse is left untouched, and `--dry-run` prints the plan without writing
or running anything. Registration goes through each host's own `mcp add` command so the
host owns its config format.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from olondunge.paths import home

HOSTS = ("claude", "codex", "grok")
SKILLS = ("olondunge", "tristack-compiler")
SERVER_NAME = "olondunge"
HOOK_TIMEOUT_S = 20
Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


@dataclass
class Step:
    host: str
    action: str
    status: str  # planned, done, skipped, failed
    detail: str


@dataclass
class Report:
    dry_run: bool
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, host: str, action: str, status: str, detail: str) -> None:
        self.steps.append(Step(host, action, status, detail))

    @property
    def failed(self) -> bool:
        return any(step.status == "failed" for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "steps": [step.__dict__ for step in self.steps],
            "notes": list(self.notes),
        }


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=120, check=False)


def user_home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw).expanduser() if raw else user_home() / ".codex"


def skills_source() -> Path:
    packaged = resources.files("olondunge").joinpath("skills")
    candidate = Path(str(packaged))
    if candidate.is_dir():
        return candidate
    return Path(__file__).resolve().parents[2] / "skills"


def entry_point(name: str) -> str | None:
    """An installed console script, preferring the one next to this interpreter so a host
    runs the same install that ran setup, not another copy found first on PATH."""

    sibling = Path(sys.executable).parent / name
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return shutil.which(name)


def server_registered(host: str, argv: Sequence[str], timeout_s: float = 20.0) -> bool | None:
    """Whether `host` knows the olondunge server, by asking that host's own CLI. None when the
    CLI is missing or answers in a way this cannot read, so doctor never guesses."""

    if shutil.which(argv[0]) is None:
        return None
    try:
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if argv[-1] == SERVER_NAME:
        return proc.returncode == 0
    return SERVER_NAME in proc.stdout


def server_command() -> list[str]:
    """The command a host runs to start the server: the installed entry point, else this
    interpreter with `-m olondunge`."""

    found = entry_point("olondunge")
    if found:
        return [found, "serve"]
    return [sys.executable, "-m", "olondunge", "serve"]


def install_source() -> str:
    """The `uv tool install` source for this package, read from its own metadata so the
    repository location is written in one place (pyproject.toml)."""

    try:
        urls = metadata.metadata("olondunge").get_all("Project-URL") or []
    except metadata.PackageNotFoundError:
        return "olondunge"
    for entry in (u.split(",", 1)[-1].strip() for u in urls):
        if entry.startswith("https://github.com/"):
            return f"git+{entry}"
    return "olondunge"


def hook_command(host: str) -> str:
    python = sys.executable
    quoted = f'"{python}"' if " " in python else python
    return f"{quoted} -m olondunge hook {host}"


def ephemeral_interpreter() -> bool:
    text = sys.executable.replace("\\", "/")
    return "/archive-v" in text or "/.cache/uv/" in text or "/uv/cache/" in text


class Setup:
    def __init__(self, *, dry_run: bool, force: bool, runner: Runner | None = None) -> None:
        self.report = Report(dry_run=dry_run)
        self.dry_run = dry_run
        self.force = force
        self.runner = runner or _run
        self.stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    # helpers ----------------------------------------------------------------------------

    def backup_root(self) -> Path:
        return home() / "backups" / self.stamp

    def backup(self, path: Path, label: str) -> Path | None:
        if not path.exists():
            return None
        target = self.backup_root() / label
        if self.dry_run:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, target, symlinks=True)
        else:
            shutil.copy2(path, target)
        return target

    def command(self, host: str, action: str, argv: list[str]) -> bool:
        shown = " ".join(argv)
        if self.dry_run:
            self.report.add(host, action, "planned", shown)
            return True
        try:
            proc = self.runner(argv)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.report.add(host, action, "failed", f"{shown}: {type(exc).__name__}")
            return False
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
            self.report.add(
                host, action, "failed", f"{shown}: exit {proc.returncode} {tail[0][:200]}"
            )
            return False
        self.report.add(host, action, "done", shown)
        return True

    # steps ------------------------------------------------------------------------------

    def install_skills(self, host: str, dest_root: Path) -> None:
        source = skills_source()
        for name in SKILLS:
            src = source / name
            dest = dest_root / name
            if not (src / "SKILL.md").is_file():
                self.report.add(host, f"skill {name}", "failed", f"packaged skill missing at {src}")
                continue
            if dest.exists() and _same_tree(src, dest):
                self.report.add(host, f"skill {name}", "skipped", f"already current at {dest}")
                continue
            backup = self.backup(dest, f"{host}-skills/{name}")
            detail = f"{src} -> {dest}" + (f" (backup {backup})" if backup else "")
            if self.dry_run:
                self.report.add(host, f"skill {name}", "planned", detail)
                continue
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest)
            self.report.add(host, f"skill {name}", "done", detail)

    def add_json_hook(self, host: str, path: Path, label: str) -> None:
        command = hook_command(host)
        data: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            except ValueError:
                self.report.add(
                    host, "tri hook", "failed", f"{path} is not valid JSON; left untouched"
                )
                return
            if not isinstance(loaded, dict):
                self.report.add(
                    host, "tri hook", "failed", f"{path} is not a JSON object; left untouched"
                )
                return
            data = loaded
        hooks = data.setdefault("hooks", {})
        groups = hooks.setdefault("UserPromptSubmit", []) if isinstance(hooks, dict) else None
        if not isinstance(groups, list):
            self.report.add(host, "tri hook", "failed", f"{path} has an unexpected hooks shape")
            return
        commands = [
            str(h.get("command", ""))
            for g in groups
            if isinstance(g, dict)
            for h in (g.get("hooks") or [])
            if isinstance(h, dict)
        ]
        if any("olondunge" in c and f"hook {host}" in c for c in commands):
            self.report.add(host, "tri hook", "skipped", f"already registered in {path}")
            return
        other = [c for c in commands if "tristack_forge" in c or "tri_hook" in c]
        if other and not self.force:
            self.report.add(
                host,
                "tri hook",
                "skipped",
                f"{path} already has a tri hook ({other[0][:80]}); rerun with --force to add "
                "Olondunge's as well (two hooks would inject the contract twice)",
            )
            return
        groups.append(
            {"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}]}
        )
        backup = self.backup(path, label)
        detail = f"{path}: {command}" + (f" (backup {backup})" if backup else "")
        if self.dry_run:
            self.report.add(host, "tri hook", "planned", detail)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".olondunge.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        self.report.add(host, "tri hook", "done", detail)

    def claude(self) -> None:
        if shutil.which("claude") is None:
            self.report.add("claude", "detect", "skipped", "claude is not on PATH")
            return
        server = server_command()
        # A dry run calls no host CLI, so it cannot know whether olondunge is registered; the
        # planned `mcp add` notes that a real run checks first.
        exists = (
            not self.dry_run and self.runner(["claude", "mcp", "get", SERVER_NAME]).returncode == 0
        )
        if exists and not self.force:
            self.report.add(
                "claude",
                "mcp add",
                "skipped",
                "olondunge is already registered (use --force to replace)",
            )
        else:
            if exists:
                self.command(
                    "claude", "mcp remove", ["claude", "mcp", "remove", "-s", "user", SERVER_NAME]
                )
            self.command(
                "claude",
                "mcp add",
                ["claude", "mcp", "add", "-s", "user", SERVER_NAME, "--", *server],
            )
            if self.dry_run:
                step = self.report.steps[-1]
                step.detail += " (a real run skips this when `claude mcp get olondunge` finds it)"
        self.install_skills("claude", user_home() / ".claude" / "skills")
        self.add_json_hook(
            "claude", user_home() / ".claude" / "settings.json", "claude-settings.json"
        )

    def codex(self) -> None:
        if shutil.which("codex") is None:
            self.report.add("codex", "detect", "skipped", "codex is not on PATH")
            return
        # `codex mcp add` refuses a CODEX_HOME that does not exist yet (OBSERVED 2026-09-16 on
        # codex-cli 0.154.0), which is the state of a fresh install that never started codex.
        if not self.dry_run:
            codex_home().mkdir(parents=True, exist_ok=True)
        self.command(
            "codex", "mcp add", ["codex", "mcp", "add", SERVER_NAME, "--", *server_command()]
        )
        self.install_skills("codex", codex_home() / "skills")
        self.add_json_hook("codex", codex_home() / "hooks.json", "codex-hooks.json")
        self.command("codex", "enable hooks", ["codex", "features", "enable", "hooks"])
        self.report.notes.append(
            "codex: new hooks must be trusted once; start codex and approve the olondunge "
            "UserPromptSubmit hook when it asks (or review it with /hooks)"
        )

    def grok(self) -> None:
        if shutil.which("grok") is None:
            self.report.add("grok", "detect", "skipped", "grok is not on PATH")
            return
        self.command(
            "grok",
            "mcp add",
            ["grok", "mcp", "add", "-s", "user", SERVER_NAME, "--", *server_command()],
        )
        self.install_skills("grok", user_home() / ".grok" / "skills")
        entry = entry_point("tri-grok")
        self.report.notes.append(
            "grok: Grok discards hook output, so `tri:` runs through the entry point: "
            + (
                f"`{entry} 'tri: <idea>'`"
                if entry
                else f"`{sys.executable} -m olondunge tri-grok 'tri: <idea>'`"
            )
        )

    def run(self, hosts: Sequence[str]) -> Report:
        if ephemeral_interpreter():
            self.report.notes.append(
                "this interpreter lives in a uv cache that can be cleaned; for a stable install "
                f"run `uv tool install {install_source()}` and then `olondunge setup` again"
            )
        for host in hosts:
            getattr(self, host)()
        return self.report


def _same_tree(a: Path, b: Path) -> bool:
    files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    if files_a != files_b:
        return False
    return all((a / rel).read_bytes() == (b / rel).read_bytes() for rel in files_a)


def run_setup(target: str, *, dry_run: bool, force: bool, runner: Runner | None = None) -> Report:
    hosts = HOSTS if target == "all" else (target,)
    return Setup(dry_run=dry_run, force=force, runner=runner).run(hosts)
