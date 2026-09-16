"""`tri:` activation for each host.

Claude Code and the Codex CLI run a UserPromptSubmit hook: a prompt that opens with `tri:`
is compiled into a full contract and injected into that same turn. Grok discards an
allowing hook's output, so Grok gets an entry point instead (`tri-grok`), which compiles the
contract and passes it to `grok` as the prompt argument.

Every path fails open: any error prints nothing and exits 0, because a hook that can strand
a turn is worse than a hook that misses one. `TRISTACK_FORGE_HOOK=0` (or
`OLONDUNGE_TRI_HOOK=0`) disables the hooks for a session.

Standard library only: the plugin hook imports this module with a bare `python3`.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import IO, Any

from olondunge.tristack.core import TRIGGER, compile_trigger, parse_trigger

HOOK_EVENT = "UserPromptSubmit"


def disabled() -> bool:
    return (
        os.environ.get("TRISTACK_FORGE_HOOK") == "0" or os.environ.get("OLONDUNGE_TRI_HOOK") == "0"
    )


def _payload(raw: str) -> dict[str, Any] | None:
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def claude_output(raw: str) -> str:
    """Claude Code reads a UserPromptSubmit hook's plain stdout as context for the turn.

    The event guard is permissive when the field is absent, because a strict test would
    silently disable the hook on a payload shape that omits it."""

    if disabled():
        return ""
    data = _payload(raw)
    if data is None:
        return ""
    event = data.get("hook_event_name")
    if event is not None and event != HOOK_EVENT:
        return ""
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return ""
    contract = compile_trigger(prompt, "claude")
    return "" if contract is None else contract + "\n"


def codex_output(raw: str) -> str:
    """The Codex CLI reads a JSON object from a hook and routes several events through one
    hooks.json, so the event name is required here."""

    if disabled():
        return ""
    data = _payload(raw)
    if data is None or data.get("hook_event_name") != HOOK_EVENT:
        return ""
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return ""
    contract = compile_trigger(prompt, "codex")
    if contract is None:
        return ""
    envelope = {
        "continue": True,
        "hookSpecificOutput": {"hookEventName": HOOK_EVENT, "additionalContext": contract},
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n"


def run_hook(host: str, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    try:
        raw = source.read()
        text = (
            claude_output(raw) if host == "claude" else codex_output(raw) if host == "codex" else ""
        )
        if text and sink is sys.stdout:
            write_stdout(text)
        elif text:
            sink.write(text)
            sink.flush()
    except Exception:  # noqa: BLE001 - fail open, never strand a turn
        pass
    return 0


GROK_USAGE = """\
usage: tri-grok [--dry-run] [--headless] '<prompt>'

  tri-grok 'tri: build a widget'           execute end to end in an interactive grok session
  tri-grok 'tri plan: migrate the tables'  plan only
  tri-grok 'tri local: fix the test'       local execution only
  tri-grok --dry-run 'tri: build a widget'  print what grok would receive and exit
  tri-grok --headless 'tri: build a widget' grok --always-approve -p (no TUI)

A prompt that opens with `tri:`, `tri plan:` or `tri local:` is compiled into the Grok edition
of the tri-stack contract. Any other prompt reaches grok unchanged, exactly as if you had run
grok yourself. Lines starting with @context:, @constraints:, @runtime:, @budget: or
@deadline: are read as fields when the prompt is one quoted multi-line argument.
"""


def write_stdout(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        # A reader such as `head` or `grep -q` closed the pipe; the text was not wanted.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())


def grok_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry = headless = False
    # Only tri-grok's own flags are consumed; anything else, `--verbose ...` included, starts
    # the prompt, which reaches grok unchanged.
    while args and args[0] in {"--dry-run", "--headless", "--"}:
        flag = args.pop(0)
        if flag == "--":
            break
        dry = dry or flag == "--dry-run"
        headless = headless or flag == "--headless"
    if not args or (len(args) == 1 and args[0] in {"-h", "--help"}):
        sys.stderr.write(GROK_USAGE)
        return 2
    text = " ".join(args)
    if parse_trigger(text) is None and TRIGGER.match(text) is not None:
        sys.stderr.write("tri-grok: the tri: line has no idea after the colon\n")
        return 1
    # A trigger compiles; anything else passes through untouched, so the contract header
    # always quotes exactly what the user typed.
    prompt = compile_trigger(text, "grok") or text
    if dry:
        write_stdout(prompt)
        return 0
    binary = os.environ.get("TRI_GROK_BIN") or shutil.which("grok")
    if binary is None:
        sys.stderr.write("tri-grok: grok was not found on PATH (set TRI_GROK_BIN)\n")
        return 127
    # One argv element, never a pipe or a file the agent is told to read: either would
    # cost the contract its owner provenance. `--always-approve` must precede `-p`, whose
    # value is taken immediately.
    command = [binary, "--always-approve", "-p", prompt] if headless else [binary, prompt]
    os.execv(binary, command)
    return 0  # pragma: no cover - execv does not return
