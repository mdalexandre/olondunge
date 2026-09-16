"""Command line entry points: `olondunge` and `tri-grok`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from olondunge import __version__


def _print_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2) + "\n")


def _serve(_: argparse.Namespace) -> int:
    from olondunge.server import main as serve_main

    serve_main()
    return 0


def _status(_: argparse.Namespace) -> int:
    from olondunge.surface import Surface

    _print_json(Surface().status())
    return 0


def _doctor(_: argparse.Namespace) -> int:
    from olondunge import setup
    from olondunge.paths import home
    from olondunge.surface import Surface

    status = Surface().status()
    hosts: dict[str, Any] = {}
    skill_roots = {
        "claude": setup.user_home() / ".claude" / "skills",
        "codex": setup.codex_home() / "skills",
        "grok": setup.user_home() / ".grok" / "skills",
    }
    for host, root in skill_roots.items():
        hosts[host] = {
            "binary": shutil.which(host),
            "skills_installed": {
                name: (root / name / "SKILL.md").is_file() for name in setup.SKILLS
            },
        }
    for host, argv in (
        ("claude", ["claude", "mcp", "get", setup.SERVER_NAME]),
        ("codex", ["codex", "mcp", "get", setup.SERVER_NAME]),
        ("grok", ["grok", "mcp", "list"]),
    ):
        hosts[host]["mcp_registered"] = setup.server_registered(host, argv)
    for host, path in (
        ("claude", setup.user_home() / ".claude" / "settings.json"),
        ("codex", setup.codex_home() / "hooks.json"),
    ):
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        hosts[host]["tri_hook"] = f"hook {host}" in text and "olondunge" in text
    hosts["grok"]["tri_entry_point"] = setup.entry_point("tri-grok")
    report = {
        "status": status.get("status"),
        "version": __version__,
        "python": sys.version.split()[0],
        "home": str(home()),
        "server_command": setup.server_command(),
        "skills_source": str(setup.skills_source()),
        "lanes": [
            {k: lane.get(k) for k in ("agent_id", "up", "reason")}
            for lane in status.get("lanes", [])
        ],
        "hosts": hosts,
        "notes": [
            f"{host}: the olondunge server is not registered; run `olondunge setup {host}`, "
            "or install the plugin, then restart the host"
            for host, info in hosts.items()
            if info["binary"] and info["mcp_registered"] is False
        ],
    }
    _print_json(report)
    return 0 if status.get("status") in {"ok", "ok_with_warnings"} else 1


def _setup(args: argparse.Namespace) -> int:
    from olondunge.setup import run_setup

    report = run_setup(args.target, dry_run=args.dry_run, force=args.force)
    if args.json:
        _print_json(report.as_dict())
    else:
        marks = {"done": "done", "planned": "plan", "skipped": "skip", "failed": "FAIL"}
        for step in report.steps:
            mark = marks.get(step.status, step.status)
            sys.stdout.write(f"[{mark}] {step.host} {step.action}: {step.detail}\n")
        for note in report.notes:
            sys.stdout.write(f"note: {note}\n")
    return 1 if report.failed else 0


def _tri(args: argparse.Namespace) -> int:
    from olondunge.tristack.core import compile_trigger

    text = " ".join(args.text) if args.text else sys.stdin.read()
    contract = compile_trigger(text, args.host)
    if contract is None:
        sys.stderr.write(
            "olondunge tri: the text does not open with tri:, tri plan: or tri local:\n"
        )
        return 1
    if args.output:
        Path(args.output).write_text(contract, encoding="utf-8")
    else:
        from olondunge.hooks import write_stdout

        write_stdout(contract)
    return 0


def _hook(args: argparse.Namespace) -> int:
    from olondunge.hooks import run_hook

    return run_hook(args.host)


def _tri_grok(args: argparse.Namespace) -> int:
    from olondunge.hooks import grok_main

    return grok_main(args.rest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="olondunge",
        description="Let the model inside Claude Code, Codex or Grok allocate work to the agent "
        "CLIs on this machine, choosing lane, model and effort, with blind verification.",
    )
    parser.add_argument("--version", action="version", version=f"olondunge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the MCP server on stdio").set_defaults(func=_serve)
    sub.add_parser("status", help="probe every lane and print alloc_status").set_defaults(
        func=_status
    )
    sub.add_parser("doctor", help="check lanes, skills and hooks for each host").set_defaults(
        func=_doctor
    )

    setup_parser = sub.add_parser("setup", help="register the server, skills and tri activation")
    setup_parser.add_argument("target", choices=["claude", "codex", "grok", "all"])
    setup_parser.add_argument(
        "--dry-run", action="store_true", help="print the plan; change nothing"
    )
    setup_parser.add_argument(
        "--force", action="store_true", help="replace an existing registration"
    )
    setup_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    setup_parser.set_defaults(func=_setup)

    tri_parser = sub.add_parser("tri", help="compile a tri: line into a contract")
    tri_parser.add_argument("--host", choices=["claude", "codex", "grok"], default="claude")
    tri_parser.add_argument("--output", help="write the contract to this file")
    tri_parser.add_argument("text", nargs="*", help="the tri: line (read from stdin when absent)")
    tri_parser.set_defaults(func=_tri)

    hook_parser = sub.add_parser("hook", help="UserPromptSubmit hook body (reads stdin)")
    hook_parser.add_argument("host", choices=["claude", "codex"])
    hook_parser.set_defaults(func=_hook)

    grok_parser = sub.add_parser("tri-grok", help="compile a tri: line and start grok with it")
    grok_parser.add_argument("rest", nargs=argparse.REMAINDER)
    grok_parser.set_defaults(func=_tri_grok)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw[:1] == ["tri-grok"]:
        # argparse's REMAINDER refuses a leading option such as --dry-run, so the entry
        # point's own parser takes everything after the subcommand.
        from olondunge.hooks import grok_main

        return grok_main(raw[1:])
    args = build_parser().parse_args(raw)
    result: int = args.func(args)
    return result


def tri_grok_main() -> int:
    from olondunge.hooks import grok_main

    return grok_main()
