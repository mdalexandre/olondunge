from olondunge.lanes.base import Health
from olondunge.registry.schema import default_rows
from olondunge.registry.select import resolve_effort, select_lane

ALL_UP = {"claude": True, "codex": True, "grok": True, "local": False}


def test_default_picks_cheapest_capable_lane_and_an_independent_verifier() -> None:
    plan = select_lane(
        {"objective": "x", "expected_output": "a code patch"}, default_rows(), ALL_UP
    )
    assert plan.status == "ok"
    assert plan.producer == "grok"
    assert plan.verifier not in (None, "grok")
    assert plan.dropped["local"].startswith("down")


def test_lane_model_and_effort_pin() -> None:
    packet = {"objective": "x", "lane": "claude", "model": "opus", "effort": "max"}
    plan = select_lane(packet, default_rows(), ALL_UP)
    assert (plan.status, plan.producer, plan.model, plan.effort) == ("ok", "claude", "opus", "max")


def test_model_routes_to_the_lane_that_owns_it() -> None:
    plan = select_lane({"objective": "x", "model": "gpt-5.5"}, default_rows(), ALL_UP)
    assert plan.producer == "codex"
    assert "does not belong" in plan.dropped["grok"]


def test_invalid_effort_on_pinned_lane_is_blocked_with_valid_values() -> None:
    plan = select_lane({"objective": "x", "lane": "grok", "effort": "max9"}, default_rows(), ALL_UP)
    assert plan.status == "blocked"
    assert "valid: low, medium, high, xhigh" in plan.reason


def test_portable_top_and_max() -> None:
    assert resolve_effort("top", ["low", "medium", "high", "xhigh"]) == "xhigh"
    assert resolve_effort("max", ["low", "high", "xhigh"]) == "xhigh"
    assert resolve_effort("max", ["low", "max", "ultra"]) == "max"
    assert resolve_effort("top", ["low", "max", "ultra"]) == "ultra"
    assert resolve_effort("high", []) is None


def test_reasoning_effort_alias_and_catalog_efforts() -> None:
    packet = {"objective": "x", "lane": "codex", "reasoning_effort": "ultra"}
    plan = select_lane(packet, default_rows(), ALL_UP, lane_efforts={"codex": ["low", "ultra"]})
    assert (plan.status, plan.effort) == ("ok", "ultra")


def test_single_lane_verifies_itself_with_a_warning() -> None:
    # REQ-05: one installed CLI still plans, verifying in a fresh session on itself.
    health = {
        "claude": Health(False, "not found", None),
        "codex": False,
        "grok": True,
        "local": False,
    }
    plan = select_lane({"objective": "x"}, default_rows(), health)
    assert plan.status == "ok_with_warnings"
    assert plan.producer == "grok" and plan.verifier == "grok"
    assert any("fresh session" in w for w in plan.warnings)


def test_verifier_pin_and_unknown_lane() -> None:
    plan = select_lane({"objective": "x", "verifier_lane": "codex"}, default_rows(), ALL_UP)
    assert plan.verifier == "codex"
    blocked = select_lane({"objective": "x", "lane": "nope"}, default_rows(), ALL_UP)
    assert blocked.status == "blocked" and "known lanes" in blocked.reason


def test_unknown_tool_vocabulary_drops_every_lane_with_reasons() -> None:
    plan = select_lane({"objective": "x", "permitted_tools": ["cdp"]}, default_rows(), ALL_UP)
    assert plan.status == "blocked"
    assert all("lacks tools" in plan.dropped[lane] for lane in ("claude", "codex", "grok"))


def test_exclude_producer_falls_back_with_warning() -> None:
    health = {"claude": False, "codex": False, "grok": True, "local": False}
    packet = {"objective": "x", "capabilities": ["independent_verification"]}
    plan = select_lane(packet, default_rows(), health, exclude_producer="grok")
    assert plan.producer == "grok"
    assert any("fresh session" in w for w in plan.warnings)
