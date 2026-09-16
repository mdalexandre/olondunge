"""Claude Code lane: `claude -p` with JSON output."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from olondunge.jobs import store
from olondunge.lanes import process
from olondunge.lanes.base import Brief, Health, JobId

WORKER_RULES = (
    "You are an Olondunge worker running one bounded task without a human in the loop. "
    "Do only the task in the prompt. Report what you actually did and observed. Do not "
    "claim a check passed unless you ran it. Never read, print, or store credentials."
)
READ_TOOLS = ("Read", "Glob", "Grep")
# A worker does one bounded task itself. A helper subagent would spend the owner's plan on
# work nobody planned (OBSERVED 2026-09-16: a read only worker tried one to get around a
# write denial), so the delegation tools stay off.
NO_DELEGATION = ("Task", "Agent")


def allowed_tools(brief: Brief) -> list[str]:
    permitted = set(brief.permitted_tools)
    tools = list(READ_TOOLS)
    if "WebSearch" in permitted:
        tools += ["WebSearch", "WebFetch"]
    if brief.write_allowed:
        if permitted & {"Write", "Edit"}:
            tools += ["Edit", "Write"]
        if "Bash" in permitted:
            tools.append("Bash")
    return tools


def build_argv(brief: Brief) -> list[str]:
    # A prompt that starts with "-" would be parsed as a flag; a leading newline is inert.
    prompt = "\n" + brief.prompt if brief.prompt.startswith("-") else brief.prompt
    argv = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "project",
        "--append-system-prompt",
        WORKER_RULES,
        "--allowedTools",
        ",".join(allowed_tools(brief)),
        "--disallowedTools",
        ",".join((*NO_DELEGATION, *process.DENY_RULES)),
    ]
    if brief.model:
        argv += ["--model", brief.model]
    if brief.effort:
        argv += ["--effort", brief.effort]
    if brief.write_allowed:
        argv += ["--permission-mode", "acceptEdits"]
    for directory in brief.read_dirs:
        argv += ["--add-dir", directory]
    return argv


def worker_env() -> dict[str, str]:
    # A nested `claude` that inherits the host session's variables attaches to that session.
    return process.scrub_host_session_env(os.environ.copy())


def parse(job_dir: Path, stdout: str, stderr: str) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Return (reply, usage, defects) from claude's JSON result."""

    del job_dir, stderr
    payload: dict[str, Any] | None = None
    for candidate in [stdout.strip(), *reversed(stdout.strip().splitlines())]:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            payload = data
            break
    if payload is None:
        return stdout.strip(), None, []
    result = payload.get("result")
    reply = result if isinstance(result, str) else ("" if result is None else str(result))
    usage: dict[str, Any] = {}
    raw_usage = payload.get("usage")
    if isinstance(raw_usage, dict):
        for key in ("input_tokens", "output_tokens"):
            if isinstance(raw_usage.get(key), int):
                usage[key] = raw_usage[key]
    if isinstance(payload.get("total_cost_usd"), (int, float)):
        usage["cost_usd"] = payload["total_cost_usd"]
    defects: list[str] = []
    if payload.get("is_error") is True:
        defects.append(f"claude reported an error result: {payload.get('subtype') or 'error'}")
    return reply.strip(), usage or None, defects


class ClaudeLane:
    name = "claude"
    binary = "claude"

    def health(self) -> Health:
        return process.probe_binary(self.binary)

    def submit(self, brief: Brief) -> JobId:
        job_dir = store.create_job_dir()
        return process.launch(
            lane=self.name,
            binary=self.binary,
            argv=build_argv(brief),
            brief=brief,
            job_dir=job_dir,
            env=worker_env(),
        )
