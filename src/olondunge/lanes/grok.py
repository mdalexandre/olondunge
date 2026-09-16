"""Grok CLI lane: single turn from a prompt file with plain output."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from olondunge.jobs import store
from olondunge.lanes import process
from olondunge.lanes.base import Brief, Health, JobId

# Grok stops at the first tool call of a non-interactive run unless approvals are
# automatic, and then returns nothing. So every worker runs with --always-approve, and
# what it may do is bounded by deny rules instead: the shared list always, plus the
# write-capable tools when the job has no write authority. Grok validates each rule's tool
# prefix and refuses to start on an unknown one (OBSERVED 2026-09-16: NotebookEdit is
# rejected), so only names the Grok CLI accepts belong here.
READ_ONLY_DENY = ("Write", "Edit", "MultiEdit", "Bash")


def build_argv(brief: Brief) -> list[str]:
    if brief.prompt_file is None:
        raise ValueError("the grok lane needs a prompt file")
    argv = [
        "grok",
        "--prompt-file",
        str(brief.prompt_file),
        "--cwd",
        str(brief.cwd),
        "--max-turns",
        str(process.max_turns_of(brief)),
        "--output-format",
        "plain",
        "--always-approve",
    ]
    if brief.model:
        argv += ["--model", brief.model]
    if brief.effort:
        argv += ["--reasoning-effort", brief.effort]
    if "WebSearch" not in brief.permitted_tools:
        argv.append("--disable-web-search")
    rules = list(process.DENY_RULES)
    if not brief.write_allowed:
        rules += list(READ_ONLY_DENY)
    for rule in rules:
        argv += ["--deny", rule]
    return argv


def parse(job_dir: Path, stdout: str, stderr: str) -> tuple[str, dict[str, Any] | None, list[str]]:
    del job_dir, stderr
    return stdout.strip(), None, []


class GrokLane:
    name = "grok"
    binary = "grok"

    def health(self) -> Health:
        return process.probe_binary(self.binary)

    def submit(self, brief: Brief) -> JobId:
        job_dir = store.create_job_dir()
        if brief.prompt_file is None:
            prompt_file = job_dir / "prompt.md"
            prompt_file.write_text(brief.prompt, encoding="utf-8")
            prompt_file.chmod(0o600)
            brief.prompt_file = prompt_file
        return process.launch(
            lane=self.name,
            binary=self.binary,
            argv=build_argv(brief),
            brief=brief,
            job_dir=job_dir,
        )
