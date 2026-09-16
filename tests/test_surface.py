from __future__ import annotations

from pathlib import Path
from typing import Any

from olondunge.surface import Surface

CATALOG: dict[str, Any] = {
    "status": "ok",
    "source": "test",
    "models": [
        {"slug": "gpt-a", "selectable": True, "priority": 2, "efforts": ["low", "medium", "high"]},
        {
            "slug": "gpt-b",
            "selectable": True,
            "priority": 1,
            "efforts": ["low", "medium", "high", "xhigh", "ultra"],
        },
        {"slug": "gpt-hidden", "selectable": False, "priority": 0, "efforts": ["low"]},
    ],
}


def test_models_reports_lanes_and_catalog() -> None:
    out = Surface(catalog_loader=lambda refresh: dict(CATALOG)).models()
    assert out["status"] == "ok"
    assert out["lanes"]["claude"]["efforts"][-1] == "max"
    assert [m["slug"] for m in out["models"]][:2] == ["gpt-a", "gpt-b"]


def test_models_without_catalog_warns() -> None:
    out = Surface().models()
    assert out["status"] == "ok_with_warnings"
    assert out["codex_catalog_status"] == "blocked"


def test_preflight_codex_uses_catalog_default_and_top_effort(fake_bin: Path) -> None:
    out = Surface(catalog_loader=lambda refresh: dict(CATALOG)).preflight("codex")
    pre = out["runtime_preflight"]
    assert pre["worker"]["exact_model_or_worker_id"] == "gpt-b"
    assert pre["worker"]["reasoning_effort"] == "medium"
    assert pre["specialist"]["reasoning_effort"] == "ultra"
    assert pre["external_lanes"]["status"] == "AVAILABLE"


def test_preflight_claude_and_bad_roles(fake_bin: Path) -> None:
    surface = Surface()
    claude = surface.preflight("claude")["runtime_preflight"]
    assert claude["specialist"]["reasoning_effort"] == "max"
    bad = surface.preflight("claude", roles={"wizard": {"effort": "high"}})
    assert bad["status"] == "blocked"
    assert surface.preflight("vim")["status"] == "blocked"


def test_codex_effort_comes_from_catalog_per_model(fake_bin: Path) -> None:
    surface = Surface(catalog_loader=lambda refresh: dict(CATALOG))
    ok = surface.plan({"objective": "x", "lane": "codex", "model": "gpt-b", "effort": "ultra"})
    assert (ok["status"], ok["effort"]) == ("ok", "ultra")
    refused = surface.plan({"objective": "x", "lane": "codex", "model": "gpt-a", "effort": "ultra"})
    assert refused["status"] == "blocked" and "valid: low, medium, high" in refused["reason"]


def test_call_validates_worker_brief_and_effort(fake_bin: Path, tmp_path: Path) -> None:
    surface = Surface()
    brief = tmp_path / "brief.md"
    brief.write_text("say hi", encoding="utf-8")
    assert surface.call("nope", str(brief))["status"] == "blocked"
    assert surface.call("grok", str(tmp_path / "missing.md"))["status"] == "blocked"
    assert surface.call("grok", str(brief), effort="max9")["status"] == "blocked"
    started = surface.call("grok", str(brief), effort="top")
    assert started["status"] == "ok" and started["effort"] == "xhigh"
    assert surface.collect(started["job_id"], wait_s=30)["reply"] == "FAKE REPLY OK"


def test_bad_inputs_never_raise() -> None:
    surface = Surface()
    assert surface.plan("not a dict")["status"] == "blocked"
    assert surface.poll("../../etc")["status"] == "blocked"
    assert surface.collect(None)["status"] == "blocked"
    assert surface.verify({}, None)["status"] == "blocked"
