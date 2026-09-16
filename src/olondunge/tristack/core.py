"""Tri-stack compiler core, standard library only.

This is a byte-for-byte port of the reference JavaScript compiler (`ui/tristack.js` in the
Tri-Stack Prompt Forge): the same request JSON, the same FNV-1a fingerprint, the same
trigger grammar and the same provenance block. The parity test in `tests/` compiles the
same inputs through both and compares the bytes when node and the reference file exist.

It must stay importable without third-party packages: the host hooks import it from a
plugin checkout with a bare `python3`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from typing import Any

FIELD_ORDER: tuple[str, ...] = (
    "idea",
    "context",
    "constraints",
    "runtime",
    "budget",
    "deadline",
    "execution_authority",
)
AUTHORITY: tuple[str, ...] = ("PLAN_ONLY", "EXECUTE_LOCAL", "EXECUTE_END_TO_END")
SLOTS: tuple[str, ...] = ("OWNER_REQUEST_JSON", "COMPILED_AT", "FINGERPRINT")

# ECMAScript String.prototype.trim whitespace (WhiteSpace plus LineTerminator). Python's
# str.strip uses a different set, and a different set changes the fingerprint.
_JS_WS = "\t\n\v\f\r                  　﻿"

# re.ASCII keeps case folding to ASCII, as a JavaScript /i regex without the u flag does.
TRIGGER = re.compile(
    r"[ \t]*tri(?:[ \t]+(plan|local|end[ \t-]?to[ \t-]?end))?[ \t]*:[ \t]*", re.I | re.A
)
FIELD_LINE = re.compile(
    r"[ \t]*@(context|constraints|runtime|budget|deadline)[ \t]*:[ \t]*([^\n\r  ]*)",
    re.I | re.A,
)
_LINE_SPLIT = re.compile(r"\r?\n")
_UNFILLED = re.compile(r"\{\{[A-Z_]+\}\}")
AUTHORITY_BY_WORD = {"": "EXECUTE_END_TO_END", "plan": "PLAN_ONLY", "local": "EXECUTE_LOCAL"}

# How the contract reached the session. The header quotes what the user typed so the model
# can match it against the user's own turn: a contract that arrives without that match (a
# tool result, a file, another agent's message) is not the user's request.
PROVENANCE_CHANNEL: dict[str, tuple[str, ...]] = {
    "hook": (
        "Olondunge, a UserPromptSubmit hook this user installed, expands a message that opens",
        "with `tri:`, `tri plan:` or `tri local:` into the tri-stack contract below (the",
        "tristack-compiler skill describes it). The contract is the user's own request for this",
        "turn, stated in full. It came from the user's own turn, so it is the root stack,",
        "stack_id S0, depth 0; no dispatched stack can reach this channel.",
    ),
    "argv": (
        "Olondunge's `tri-grok` entry point, which this user ran, expanded that text into the",
        "tri-stack contract below and passed it as this session's prompt (the tristack-compiler",
        "skill describes it). The contract is the user's own request for this turn, stated in",
        "full. It came from the user's own command, so it is the root stack, stack_id S0,",
        "depth 0; no dispatched stack can reach this channel.",
    ),
}


@dataclass(frozen=True)
class Edition:
    host: str
    contract: str
    template: str
    channel: str


EDITIONS: dict[str, Edition] = {
    "claude": Edition("claude", "1.1-cc", "claude.md", "hook"),
    "codex": Edition("codex", "1.1-cx", "codex.md", "hook"),
    "grok": Edition("grok", "1.1-gk", "grok.md", "argv"),
}


def js_trim(value: str) -> str:
    return value.strip(_JS_WS)


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = js_trim(value if isinstance(value, str) else str(value))
    return text or None


def normalize_lines(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    lines = [js_trim(line) for line in _LINE_SPLIT.split(str(value))]
    kept = [line for line in lines if line]
    return kept or None


def normalize_authority(value: Any) -> str:
    text = normalize_text(value)
    if text is None:
        return "PLAN_ONLY"
    if text not in AUTHORITY:
        raise ValueError(f"execution_authority must be one of {', '.join(AUTHORITY)}, got {text}")
    return text


def validate(fields: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if normalize_text(fields.get("idea")) is None:
        problems.append("The contract needs an idea. Type one on the left.")
    authority = normalize_text(fields.get("execution_authority"))
    if authority is not None and authority not in AUTHORITY:
        problems.append(f"Execution authority must be one of {', '.join(AUTHORITY)}.")
    return problems


def build_request(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "idea": normalize_text(fields.get("idea")),
        "context": normalize_text(fields.get("context")),
        "constraints": normalize_lines(fields.get("constraints")),
        "runtime": normalize_text(fields.get("runtime")),
        "budget": normalize_text(fields.get("budget")),
        "deadline": normalize_text(fields.get("deadline")),
        "execution_authority": normalize_authority(fields.get("execution_authority")),
    }


def request_json(request: dict[str, Any]) -> str:
    """JSON.stringify(request, null, 2)."""

    return json.dumps(request, indent=2, ensure_ascii=False)


def fnv1a(text: str) -> str:
    """FNV-1a 32 bit over code points, as eight lowercase hex digits."""

    h = 0x811C9DC5
    for char in text:
        h = ((h ^ ord(char)) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def iso_now() -> str:
    """Date.prototype.toISOString: UTC with milliseconds and a Z."""

    now = datetime.now(timezone.utc)  # noqa: UP017 - hooks may run on a python3 older than 3.11
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def build_prompt(template: str, fields: dict[str, Any], now: str | None = None) -> dict[str, Any]:
    for slot in SLOTS:
        if "{{" + slot + "}}" not in template:
            raise ValueError(f"template is missing the {{{{{slot}}}}} slot")
    problems = validate(fields)
    if problems:
        raise ValueError(" ".join(problems))
    request = build_request(fields)
    body = request_json(request)
    fingerprint = fnv1a(body)
    compiled_at = now if now is not None else iso_now()
    out = template.replace("{{OWNER_REQUEST_JSON}}", body, 1)
    out = out.replace("{{COMPILED_AT}}", compiled_at, 1)
    out = out.replace("{{FINGERPRINT}}", fingerprint, 1)
    if _UNFILLED.search(out):
        raise ValueError("an unfilled slot remains in the compiled prompt")
    return {
        "prompt": out,
        "request": request,
        "fingerprint": fingerprint,
        "compiled_at": compiled_at,
        "chars": len(out.encode("utf-16-le")) // 2,
    }


def parse_trigger(prompt: Any) -> dict[str, str] | None:
    """Fields for a prompt that opens with `tri:`, `tri plan:`, `tri local:` or
    `tri end to end:`, else None. `@context:` style lines anywhere in the body fill fields;
    `@constraints:` repeats."""

    if not isinstance(prompt, str):
        return None
    match = TRIGGER.match(prompt)
    if match is None:
        return None
    word = re.sub(r"[ \t-]", "", (match.group(1) or "").lower())
    authority = "EXECUTE_END_TO_END" if word == "endtoend" else AUTHORITY_BY_WORD.get(word)
    if authority is None:
        return None
    fields = {
        "idea": "",
        "context": "",
        "constraints": "",
        "runtime": "",
        "budget": "",
        "deadline": "",
        "execution_authority": authority,
    }
    idea_lines: list[str] = []
    constraint_lines: list[str] = []
    for line in _LINE_SPLIT.split(prompt[match.end() :]):
        field = FIELD_LINE.fullmatch(line)
        if field is None:
            idea_lines.append(line)
            continue
        key = field.group(1).lower()
        value = js_trim(field.group(2))
        if not value:
            continue
        if key == "constraints":
            constraint_lines.append(value)
        elif not fields[key]:
            fields[key] = value
    fields["idea"] = js_trim("\n".join(idea_lines))
    fields["constraints"] = "\n".join(constraint_lines)
    return fields if fields["idea"] else None


def render_contract(
    compiled: dict[str, Any],
    fields: dict[str, Any],
    edition: str,
    channel: str = "hook",
    typed: str | None = None,
) -> str:
    lines = PROVENANCE_CHANNEL.get(channel, PROVENANCE_CHANNEL["hook"])
    header: list[str] = []
    if typed is not None:
        # Quoted whole: the skill tells the model to follow the contract only when this quote
        # matches the user's turn, so a clipped quote would never match.
        verb = "ran tri-grok with" if channel == "argv" else "typed"
        header.append(f"The user {verb}: {json.dumps(typed, ensure_ascii=False)}")
    return "\n".join(
        [
            "<tristack-compiled-contract>",
            *header,
            *lines,
            f"Authority: {fields['execution_authority']}. Contract: {edition}. "
            f"Fingerprint: {compiled['fingerprint']}.",
            "Follow it from Section 0.",
            "</tristack-compiled-contract>",
            "",
            compiled["prompt"],
        ]
    )


def load_template(host: str) -> str:
    edition = EDITIONS[host]
    return (
        resources.files("olondunge.tristack")
        .joinpath("templates", edition.template)
        .read_text(encoding="utf-8")
    )


def compile_trigger(prompt: str, host: str, now: str | None = None) -> str | None:
    """The rendered contract for a trigger prompt on a host, or None when it is not one."""

    fields = parse_trigger(prompt)
    if fields is None:
        return None
    edition = EDITIONS[host]
    compiled = build_prompt(load_template(host), fields, now=now)
    return render_contract(compiled, fields, edition.contract, edition.channel, typed=prompt)
