"""Shared plumbing for subprocess lanes: health probes, launch, and child reaping."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from olondunge.jobs import store
from olondunge.lanes.base import Brief, Health, JobId
from olondunge.paths import home, is_inside
from olondunge.redact import redact

# Agentic lane work reads files and writes them; a single completion budget starves it.
# A packet may lower either value with `timeout_s` and `tool_call_limit`.
DEFAULT_TIMEOUT_S = 900.0
MAX_TIMEOUT_S = 3600.0
DEFAULT_MAX_TURNS = 40
MAX_PROMPT_BYTES = 100_000

# Commands no worker may run, whatever the packet says. Claude receives them through
# --disallowedTools and Grok through --deny. Deny rules outrank every permission mode.
DENY_RULES: tuple[str, ...] = (
    "Bash(sudo *)",
    "Bash(su *)",
    "Bash(rm -rf *)",
    "Bash(git push *)",
    "Bash(git reset --hard *)",
    "Bash(docker *)",
    "Bash(systemctl *)",
    "Bash(shutdown *)",
    "Bash(reboot *)",
    "Bash(mkfs *)",
    "Bash(dd *)",
)

# Popen handles for jobs this server process launched. poll() on them reaps the wrap as
# soon as it exits, so a finished job never lingers as a zombie that still looks alive.
_CHILDREN: dict[str, subprocess.Popen[bytes]] = {}
_CHILDREN_LOCK = threading.Lock()


# Variables a host agent session sets for its own process tree. A worker that inherits them
# believes it belongs to that session: OBSERVED 2026-09-16, a `claude -p` worker started under
# a Claude Code session carrying CLAUDE_CODE_COORDINATOR_MODE came up with only coordinator
# tools (no Read) and refused its task. Some also carry a session messaging token, which no
# worker needs. Provider settings the user exported (for example CLAUDE_CODE_USE_BEDROCK)
# are kept.
HOST_SESSION_ENV = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES",
        "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP",
    }
)
HOST_SESSION_MARKERS = ("SESSION", "COORDINATOR", "MESSAGING", "BRIDGE", "CHILD")


def scrub_host_session_env(env: dict[str, str]) -> dict[str, str]:
    """A copy of `env` without the variables that bind a process to the host agent session."""

    return {
        key: value
        for key, value in env.items()
        if key not in HOST_SESSION_ENV
        and not (
            key.startswith("CLAUDE_CODE_") and any(mark in key for mark in HOST_SESSION_MARKERS)
        )
    }


def current_depth() -> int:
    """How deep in an allocation chain this process runs: 0 for a host session, 1 inside a
    worker. Workers inherit OLONDUNGE_DEPTH, and so does any MCP server their CLI starts."""

    try:
        return max(0, int(os.environ.get("OLONDUNGE_DEPTH", "0")))
    except ValueError:
        return 0


def max_depth() -> int:
    try:
        return max(1, int(os.environ.get("OLONDUNGE_MAX_DEPTH", "1")))
    except ValueError:
        return 1


def probe_binary(binary: str) -> Health:
    resolved = shutil.which(binary)
    if resolved is None:
        return Health(up=False, reason=f"binary {binary} not found on PATH", binary=None)
    return Health(up=True, reason="binary found", binary=resolved)


def timeout_of(brief: Brief) -> float:
    if brief.timeout_s is None or brief.timeout_s <= 0:
        return DEFAULT_TIMEOUT_S
    return min(float(brief.timeout_s), MAX_TIMEOUT_S)


def max_turns_of(brief: Brief) -> int:
    if brief.max_turns is None or brief.max_turns <= 0:
        return DEFAULT_MAX_TURNS
    return int(brief.max_turns)


def owns_directory(cwd: Path) -> bool:
    """True when cwd sits inside Olondunge's own state directory (job or scratch)."""

    return is_inside(cwd, home())


def launch(
    *,
    lane: str,
    binary: str,
    argv: list[str],
    brief: Brief,
    job_dir: Path,
    env: dict[str, str] | None = None,
    meta: dict[str, object] | None = None,
) -> JobId:
    """Record job.json, start the wrap as a session leader, and mark the job running.

    A missing binary or a launch error still produces a job, finished at once with
    `exit_code.txt` 127, so every dispatch has a job id the host can collect.
    """

    timeout_s = timeout_of(brief)
    store.begin_job(
        job_dir,
        lane=lane,
        worker=lane,
        kind=brief.kind,
        model=brief.model,
        effort=brief.effort,
        timeout_s=timeout_s,
        argv=[redact(part) for part in argv],
        run_cwd=str(brief.cwd),
        **(meta or {}),
    )
    job = JobId(value=job_dir.name, job_dir=job_dir, worker=lane)
    resolved = shutil.which(binary)
    if resolved is None:
        (job_dir / "stdout.txt").write_text("", encoding="utf-8")
        (job_dir / "stderr.txt").write_text(f"binary {binary} not found on PATH\n", "utf-8")
        (job_dir / "exit_code.txt").write_text("127", encoding="utf-8")
        store.update_job(job_dir, status="running", launch_error=f"binary {binary} not found")
        return job
    worker_argv = [resolved, *argv[1:]]
    wrap_argv = [
        sys.executable,
        "-m",
        "olondunge.jobs.wrap",
        "--job-dir",
        str(job_dir),
        "--timeout-s",
        str(timeout_s),
        "--",
        *worker_argv,
    ]
    brief.cwd.mkdir(parents=True, exist_ok=True)
    # A worker's CLI may load MCP servers, Olondunge included. The depth marker lets that
    # nested server refuse to allocate again instead of recursing.
    worker_env = scrub_host_session_env(dict(env) if env is not None else os.environ.copy())
    worker_env["OLONDUNGE_DEPTH"] = str(current_depth() + 1)
    try:
        proc = subprocess.Popen(
            wrap_argv,
            cwd=str(brief.cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=worker_env,
        )
    except OSError as exc:
        (job_dir / "stderr.txt").write_text(redact(f"launch failed: {exc}\n"), encoding="utf-8")
        (job_dir / "stdout.txt").write_text("", encoding="utf-8")
        (job_dir / "exit_code.txt").write_text("127", encoding="utf-8")
        store.update_job(job_dir, status="running", launch_error=redact(str(exc)))
        return job
    with _CHILDREN_LOCK:
        _CHILDREN[job.value] = proc
    store.update_job(job_dir, status="running", pid=proc.pid)
    return job


def reap(job_id: str, pid: int | None) -> bool:
    """Reap the wrap if it has exited. Returns True when the wrap is no longer running."""

    with _CHILDREN_LOCK:
        proc = _CHILDREN.get(job_id)
    if proc is not None:
        if proc.poll() is None:
            return False
        with _CHILDREN_LOCK:
            _CHILDREN.pop(job_id, None)
        return True
    if pid is None:
        return True
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
        if reaped:
            return True
    except ChildProcessError:
        pass
    except OSError:
        return True
    return not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        try:
            state = stat.read_text().rpartition(")")[2].split()[0]
            return state != "Z"
        except (OSError, IndexError):
            return True
    return True


def kill_job(job_id: str, pid: int | None) -> None:
    with _CHILDREN_LOCK:
        proc = _CHILDREN.pop(job_id, None)
    target = proc.pid if proc is not None else pid
    if target is None:
        return
    try:
        os.killpg(target, 9)
    except OSError:
        try:
            os.kill(target, 9)
        except OSError:
            pass
    if proc is not None:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
