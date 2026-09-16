"""Codex CLI lane: `codex exec` with the final message written to a file."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from olondunge.jobs import store
from olondunge.lanes import catalog, process
from olondunge.lanes.base import Brief, Health, JobId

LAST_MESSAGE = "last_message.txt"


def isolated() -> bool:
    """Run workers with a scratch CODEX_HOME unless OLONDUNGE_CODEX_ISOLATED=0.

    Isolation keeps the user's own MCP servers (including Olondunge itself) and hooks out
    of every worker, so a worker cannot recurse into another allocation. Authentication
    still works because the scratch home links to the user's auth file; it is linked,
    never read or copied.
    """

    return os.environ.get("OLONDUNGE_CODEX_ISOLATED", "1").strip() != "0"


def build_argv(brief: Brief, job_dir: Path) -> list[str]:
    argv = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--cd",
        str(brief.cwd),
        "--sandbox",
        "workspace-write" if brief.write_allowed else "read-only",
        "--output-last-message",
        str(job_dir / LAST_MESSAGE),
    ]
    if isolated():
        argv.append("--ignore-user-config")
    if brief.model:
        argv += ["--model", brief.model]
    if brief.effort:
        argv += ["--config", "model_reasoning_effort=" + json.dumps(brief.effort)]
    argv.append("\n" + brief.prompt if brief.prompt.startswith("-") else brief.prompt)
    return argv


def worker_env(job_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    if not isolated():
        return env
    scratch_home = job_dir / "codex_home"
    scratch_home.mkdir(parents=True, exist_ok=True)
    auth_src = catalog.codex_home() / "auth.json"
    auth_dst = scratch_home / "auth.json"
    if auth_src.exists() and not auth_dst.exists():
        try:
            auth_dst.symlink_to(auth_src)
        except OSError:
            pass
    cache_src = catalog.codex_home() / "models_cache.json"
    cache_dst = scratch_home / "models_cache.json"
    if cache_src.exists() and not cache_dst.exists():
        try:
            cache_dst.symlink_to(cache_src)
        except OSError:
            pass
    env["CODEX_HOME"] = str(scratch_home)
    return env


def parse(job_dir: Path, stdout: str, stderr: str) -> tuple[str, dict[str, Any] | None, list[str]]:
    del stderr
    last = job_dir / LAST_MESSAGE
    if last.is_file():
        text = last.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text, None, []
    return stdout.strip(), None, []


class CodexLane:
    name = "codex"
    binary = "codex"

    def health(self) -> Health:
        return process.probe_binary(self.binary)

    def submit(self, brief: Brief) -> JobId:
        job_dir = store.create_job_dir()
        return process.launch(
            lane=self.name,
            binary=self.binary,
            argv=build_argv(brief, job_dir),
            brief=brief,
            job_dir=job_dir,
            env=worker_env(job_dir),
        )
