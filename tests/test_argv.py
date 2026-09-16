from pathlib import Path

from olondunge.lanes import claude, codex, grok
from olondunge.lanes.base import Brief


def _brief(**kwargs: object) -> Brief:
    base = {"prompt": "do it", "cwd": Path("/tmp")}
    base.update(kwargs)
    return Brief(**base)  # type: ignore[arg-type]


def test_grok_websearch_grant_keeps_web_search() -> None:
    # Defect 06e: a WebSearch grant must not be disabled.
    argv = grok.build_argv(_brief(prompt_file=Path("/tmp/p.md"), permitted_tools=("WebSearch",)))
    assert "--disable-web-search" not in argv


def test_grok_without_grant_disables_web_search_and_denies_writes() -> None:
    argv = grok.build_argv(_brief(prompt_file=Path("/tmp/p.md")))
    assert "--disable-web-search" in argv
    denied = [argv[i + 1] for i, part in enumerate(argv) if part == "--deny"]
    assert {"Write", "Edit", "Bash", "Bash(sudo *)"} <= set(denied)
    # The Grok CLI refuses to start on an unknown tool prefix.
    assert "NotebookEdit" not in denied


def test_grok_model_and_effort_flags() -> None:
    argv = grok.build_argv(_brief(prompt_file=Path("/tmp/p.md"), model="grok-x", effort="high"))
    assert argv[argv.index("--model") + 1] == "grok-x"
    assert argv[argv.index("--reasoning-effort") + 1] == "high"


def test_claude_flags() -> None:
    argv = claude.build_argv(
        _brief(
            model="opus", effort="max", permitted_tools=("Read", "WebSearch"), read_dirs=("/data",)
        )
    )
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--add-dir") + 1] == "/data"
    assert "WebSearch" in argv[argv.index("--allowedTools") + 1]
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "Bash(sudo *)" in denied and {"Task", "Agent"} <= set(denied)
    assert "--permission-mode" not in argv


def test_claude_write_allowed() -> None:
    argv = claude.build_argv(_brief(write_allowed=True, permitted_tools=("Write", "Bash")))
    tools = argv[argv.index("--allowedTools") + 1]
    assert "Write" in tools and "Bash" in tools
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_leading_dash_prompt_is_not_a_flag() -> None:
    assert claude.build_argv(_brief(prompt="--help"))[2].startswith("\n")
    assert codex.build_argv(_brief(prompt="-x"), Path("/tmp"))[-1].startswith("\n")


def test_codex_flags(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("OLONDUNGE_CODEX_ISOLATED", raising=False)
    argv = codex.build_argv(_brief(model="gpt-5.5", effort="xhigh"), Path("/tmp/job"))
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--model") + 1] == "gpt-5.5"
    assert argv[argv.index("--config") + 1] == 'model_reasoning_effort="xhigh"'
    assert "--ignore-user-config" in argv
    write = codex.build_argv(_brief(write_allowed=True), Path("/tmp/job"))
    assert write[write.index("--sandbox") + 1] == "workspace-write"
