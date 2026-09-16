from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from olondunge.tristack import core

NOW = "2026-09-16T14:09:38.398Z"
# Full prompt parity needs the reference JavaScript compiler, which is not part of this
# repository: point TRISTACK_REFERENCE_JS at its tristack.js to run it.
REFERENCE_JS = Path(os.environ.get("TRISTACK_REFERENCE_JS", "/nonexistent/tristack.js"))
SAMPLES = [
    "tri: build a CLI that sorts my Downloads folder",
    "tri plan: migrate the billing tables\n@context: postgres 16\n@constraints: no downtime\n@constraints: keep ids",
    "  TRI LOCAL :  fix the parser test\n@budget: 5 USD\n@deadline: friday\nsecond line of idea",
    "tri end-to-end: ship it\n@runtime:   linux  \n@context:\n\tnote: prose with a colon",
    'tri: unicode idea é中\U0001f600 with "quotes" and \\ backslash\n@constraints:  padded ',
]


def test_trigger_grammar() -> None:
    assert core.parse_trigger("hello tri: x") is None
    assert core.parse_trigger("tri:   ") is None
    assert core.parse_trigger("tri: x")["execution_authority"] == "EXECUTE_END_TO_END"  # type: ignore[index]
    assert core.parse_trigger("tri plan: x")["execution_authority"] == "PLAN_ONLY"  # type: ignore[index]
    assert core.parse_trigger("tri local: x")["execution_authority"] == "EXECUTE_LOCAL"  # type: ignore[index]
    fields = core.parse_trigger(SAMPLES[1])
    assert fields is not None
    assert fields["constraints"] == "no downtime\nkeep ids"
    assert fields["context"] == "postgres 16"


def test_fnv1a_known_vector() -> None:
    assert core.fnv1a("") == "811c9dc5"
    assert core.fnv1a("a") == "e40c292c"


def test_every_edition_compiles_with_its_contract_and_channel() -> None:
    for host, contract, channel_word in (
        ("claude", "1.1-cc", "UserPromptSubmit hook"),
        ("codex", "1.1-cx", "UserPromptSubmit hook"),
        ("grok", "1.1-gk", "passed it as this session's prompt"),
    ):
        text = core.compile_trigger("tri: build a widget", host, now=NOW)
        assert text is not None
        assert f"Contract: {contract}." in text
        assert channel_word in text.split("</tristack-compiled-contract>")[0]
        assert "{{" not in text


def test_templates_carry_no_machine_specific_references() -> None:
    banned = ("/home/", "allocation-mcp", "claude_headless", "KHEC", "theoria", "gpt-5.6")
    for host in core.EDITIONS:
        template = core.load_template(host)
        for word in banned:
            assert word not in template, (host, word)


REFERENCE_FIXTURE = Path(__file__).parent / "fixtures" / "tristack_reference.json"


def _reference_cases() -> list[dict[str, str | None]]:
    cases: list[dict[str, str | None]] = json.loads(REFERENCE_FIXTURE.read_text("utf-8"))["cases"]
    return cases


def test_python_compiler_matches_recorded_reference_outputs() -> None:
    # Runs everywhere, CI included: the request JSON and fingerprint the reference JavaScript
    # compiler produced for each sample, recorded in tests/fixtures.
    cases = _reference_cases()
    assert sum(1 for case in cases if case["request_json"] is not None) >= 20
    for case in cases:
        sample = str(case["sample"])
        fields = core.parse_trigger(sample)
        if case["request_json"] is None:
            assert fields is None, sample
            continue
        assert fields is not None, sample
        body = core.request_json(core.build_request(fields))
        assert body.encode("utf-8") == str(case["request_json"]).encode("utf-8"), sample
        assert core.fnv1a(body) == case["fingerprint"], sample


@pytest.mark.skipif(
    shutil.which("node") is None or not REFERENCE_JS.is_file(),
    reason="set TRISTACK_REFERENCE_JS to the reference tristack.js to check full prompt parity",
)
def test_byte_parity_with_reference_javascript(tmp_path: Path) -> None:
    samples = [str(case["sample"]) for case in _reference_cases()]
    script = tmp_path / "parity.js"
    script.write_text(
        "const core = require(process.argv[2]);\n"
        "const fs = require('fs');\n"
        "const input = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        "const out = {};\n"
        "for (const [host, path] of Object.entries(input.templates)) {\n"
        "  const template = fs.readFileSync(path, 'utf8');\n"
        "  out[host] = input.samples.map(s => { const f = core.parseTrigger(s); if (!f) return null;\n"
        "    const c = core.buildPrompt(template, f, {now: process.argv[3]});\n"
        "    return {prompt: c.prompt, request_json: JSON.stringify(c.request, null, 2),\n"
        "            fingerprint: c.fingerprint}; });\n"
        "}\n"
        "process.stdout.write(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    templates = {}
    for host in ("claude", "codex", "grok"):
        path = tmp_path / f"{host}.md"
        path.write_text(core.load_template(host), encoding="utf-8")
        templates[host] = str(path)
    proc = subprocess.run(
        ["node", str(script), str(REFERENCE_JS), NOW],
        input=json.dumps({"samples": samples, "templates": templates}),
        capture_output=True,
        text=True,
        check=True,
    )
    expected = json.loads(proc.stdout)
    for host in ("claude", "codex", "grok"):
        template = core.load_template(host)
        for case, js in zip(_reference_cases(), expected[host], strict=True):
            sample = str(case["sample"])
            fields = core.parse_trigger(sample)
            if js is None:
                assert fields is None and case["request_json"] is None, sample
                continue
            assert fields is not None, sample
            # The recorded fixture is still what the reference produces.
            assert js["request_json"] == case["request_json"], sample
            compiled = core.build_prompt(template, fields, now=NOW)
            assert compiled["prompt"] == js["prompt"], (host, sample)
            assert compiled["fingerprint"] == js["fingerprint"], (host, sample)


def test_header_quotes_the_typed_trigger() -> None:
    typed = "tri plan: rename the package"
    contract = core.compile_trigger(typed, "claude")
    assert contract is not None
    head = contract.split("</tristack-compiled-contract>")[0]
    assert f"The user typed: {json.dumps(typed)}" in head
    assert "Authority: PLAN_ONLY. Contract: 1.1-cc." in head
    grok = core.compile_trigger(typed, "grok")
    assert grok is not None and "The user ran tri-grok with:" in grok
    assert "ranks directly below" not in grok and "ranks directly below" not in contract


def test_js_trim_differs_from_python_strip_where_javascript_does() -> None:
    # JavaScript trims a BOM and keeps the ASCII separators 0x1C to 0x1F; str.strip does the
    # opposite, which would change request JSON and fingerprints.
    assert core.js_trim("﻿ idea \x1c") == "idea \x1c"
    assert core.js_trim("\x1fidea　") == "\x1fidea"
    assert "﻿ idea \x1c".strip() != core.js_trim("﻿ idea \x1c")
