"""Deterministic self-allocation: which lane runs a packet, on which model and effort, and
which lane verifies it.

The calling model decides what the work needs and says so in the packet (`lane`, `model`,
`effort`, `verifier_lane`, or none of them). This module applies that decision against
what is actually installed and healthy, and it explains every lane it did not choose in
`dropped`, so a blocked plan always names what to change.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from olondunge.lanes.base import Health
from olondunge.registry.schema import COST_RANK, RegistryRow

# Ascending strength. Two portable values exist: `top` always means the lane's strongest
# effort, and `max` means the lane's own `max` when it has one, else its strongest.
EFFORT_ORDER: tuple[str, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)
PORTABLE_TOP = frozenset({"top", "max"})


@dataclass(frozen=True)
class Plan:
    status: str
    producer: str | None
    verifier: str | None
    model: str | None
    effort: str | None
    reason: str
    cost_class: str | None
    tie_break: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()
    dropped: dict[str, str] = field(default_factory=dict)
    producer_efforts: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "producer": self.producer,
            "verifier": self.verifier,
            "model": self.model,
            "effort": self.effort,
            "reason": self.reason,
            "cost_class": self.cost_class,
            "tie_break": self.tie_break,
            "warnings": list(self.warnings),
            "dropped": dict(self.dropped),
            "producer_efforts": list(self.producer_efforts),
        }


def _blocked(reason: str, dropped: dict[str, str], **extra: Any) -> Plan:
    return Plan(
        status="blocked",
        producer=extra.get("producer"),
        verifier=None,
        model=None,
        effort=None,
        reason=reason,
        cost_class=None,
        dropped=dropped,
        producer_efforts=tuple(extra.get("producer_efforts", ())),
    )


def _is_up(health: Mapping[str, Health | bool], agent_id: str) -> tuple[bool, str]:
    if agent_id not in health:
        return True, ""
    value = health[agent_id]
    if isinstance(value, Health):
        return value.up, value.reason
    return bool(value), "" if value else "lane is down"


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _text(packet: dict[str, Any], key: str) -> str | None:
    value = packet.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def needed_capabilities(packet: dict[str, Any]) -> set[str]:
    """Capabilities the packet asks for: explicit `capabilities`, else keyword inference."""

    explicit = set(_strings(packet.get("capabilities")))
    if explicit:
        return explicit
    parts = [str(packet.get("expected_output", ""))]
    parts.extend(_strings(packet.get("evidence_obligations")))
    haystack = " ".join(parts).lower()
    needed: set[str] = set()
    if any(word in haystack for word in ("research", "citation", "sources")):
        needed.add("research")
    if "triage" in haystack:
        needed.add("triage")
    if "extract" in haystack:
        needed.add("extract")
    if any(word in haystack for word in ("verdict", "verification", "verify")):
        needed.add("independent_verification")
    if any(word in haystack for word in ("implementation", "patch", "code")):
        needed.add("code")
    return needed or {"drafting"}


def _prohibition_hits(packet: dict[str, Any], row: RegistryRow) -> str | None:
    tools = set(row.permitted_tools)
    for raw in _strings(packet.get("prohibited_actions")):
        token = raw.strip().lower()
        if token in {row.transport, row.cost_class, row.agent_id}:
            return raw
        if token in {"gpu", "local_gpu"} and row.cost_class == "local_gpu":
            return raw
        if token in {cap.lower() for cap in row.capabilities} or raw in tools:
            return raw
    return None


def model_fits(row: RegistryRow, model: str) -> bool:
    if model in row.models:
        return True
    return any(re.search(pattern, model) for pattern in row.model_patterns)


def efforts_for(row: RegistryRow, overrides: Mapping[str, Sequence[str]] | None) -> tuple[str, ...]:
    if overrides is not None and row.agent_id in overrides:
        return tuple(overrides[row.agent_id])
    return row.efforts


def resolve_effort(requested: str, supported: Sequence[str]) -> str | None:
    """The lane value for a requested effort, or None when the lane cannot honour it."""

    wanted = requested.strip().lower()
    if not supported:
        return None
    if wanted in supported and wanted != "top":
        return wanted
    if wanted in PORTABLE_TOP:
        ranked = sorted(supported, key=lambda e: EFFORT_ORDER.index(e) if e in EFFORT_ORDER else -1)
        return ranked[-1]
    return None


def _producer_key(row: RegistryRow) -> tuple[int, int, str]:
    return (COST_RANK[row.cost_class], row.preferred_index, row.agent_id)


def select_lane(
    packet: dict[str, Any],
    rows: Sequence[RegistryRow],
    health: Mapping[str, Health | bool] | None = None,
    *,
    lane_efforts: Mapping[str, Sequence[str]] | None = None,
    exclude_producer: str | None = None,
) -> Plan:
    """Pick the producer lane, its model and effort, and a verifier lane.

    `lane_efforts` overrides a row's effort list (the Codex catalog knows per model).
    `exclude_producer` keeps a lane from producing, which is how a verify job avoids the
    lane that made the artifact while still falling back to it when nothing else is up.
    """

    health_map: Mapping[str, Health | bool] = health if health is not None else {}
    by_id = {row.agent_id: row for row in rows}
    pinned = _text(packet, "lane")
    model = _text(packet, "model")
    effort = _text(packet, "effort") or _text(packet, "reasoning_effort")
    if pinned is not None and pinned not in by_id:
        return _blocked(f"unknown lane {pinned}; known lanes: {', '.join(sorted(by_id))}", {})

    dropped: dict[str, str] = {}
    warnings: list[str] = []
    wanted_tools = set(_strings(packet.get("permitted_tools")))
    wanted_authority = set(_strings(packet.get("authority_ceiling")))
    needed = needed_capabilities(packet)
    survivors: list[tuple[RegistryRow, str | None]] = []

    for row in rows:
        if pinned is not None and row.agent_id != pinned:
            continue
        up, why = _is_up(health_map, row.agent_id)
        if not up:
            dropped[row.agent_id] = f"down: {why or 'health probe failed'}"
            continue
        missing_tools = wanted_tools - set(row.permitted_tools)
        if missing_tools:
            dropped[row.agent_id] = f"lacks tools {sorted(missing_tools)}"
            continue
        missing_authority = wanted_authority - row.authority_ceiling
        if missing_authority:
            dropped[row.agent_id] = f"authority ceiling lacks {sorted(missing_authority)}"
            continue
        missing_caps = needed - row.capabilities
        if missing_caps:
            if pinned is None:
                dropped[row.agent_id] = f"lacks capabilities {sorted(missing_caps)}"
                continue
            warnings.append(f"pinned lane {pinned} lacks capabilities {sorted(missing_caps)}")
        hit = _prohibition_hits(packet, row)
        if hit is not None:
            dropped[row.agent_id] = f"prohibited by packet: {hit}"
            continue
        if model is not None and not model_fits(row, model):
            if pinned is None:
                dropped[row.agent_id] = f"model {model} does not belong to this lane"
                continue
            warnings.append(
                f"model {model} does not match lane {pinned}'s known models; passed through"
            )
        resolved: str | None = None
        if effort is not None:
            supported = efforts_for(row, lane_efforts)
            resolved = resolve_effort(effort, supported)
            if resolved is None:
                valid = ", ".join(supported) if supported else "none (no effort control)"
                dropped[row.agent_id] = f"effort {effort} not supported; valid: {valid}"
                continue
        survivors.append((row, resolved))

    producers = [pair for pair in survivors if pair[0].agent_id != exclude_producer]
    if not producers and survivors:
        producers = survivors
        warnings.append(
            f"no lane other than {exclude_producer} can run this; it runs in a fresh session there"
        )
    if not producers:
        if pinned is not None:
            return _blocked(
                f"pinned lane {pinned} cannot run this packet: {dropped.get(pinned, 'filtered')}",
                dropped,
                producer_efforts=efforts_for(by_id[pinned], lane_efforts),
            )
        return _blocked("no lane survived the packet filter and health", dropped)

    producers.sort(key=lambda pair: _producer_key(pair[0]))
    best = producers[0][0]
    tied = [
        pair[0].agent_id
        for pair in producers
        if (COST_RANK[pair[0].cost_class], pair[0].preferred_index)
        == (COST_RANK[best.cost_class], best.preferred_index)
    ]
    tie_break = (
        {"method": "stable_agent_id", "candidates": tied, "choice": best.agent_id}
        if len(tied) > 1
        else None
    )
    producer, producer_effort = producers[0]
    for row in rows:
        if row.agent_id not in dropped and row.agent_id != producer.agent_id:
            if pinned is None and any(pair[0].agent_id == row.agent_id for pair in producers):
                dropped[row.agent_id] = "survived; not chosen (cost, preference, id order)"

    verifier, verifier_warning = _pick_verifier(packet, rows, health_map, producer)
    if verifier_warning:
        warnings.append(verifier_warning)
    return Plan(
        status="ok_with_warnings" if warnings else "ok",
        producer=producer.agent_id,
        verifier=verifier,
        model=model or producer.default_model,
        effort=producer_effort or producer.default_effort,
        reason="",
        cost_class=producer.cost_class,
        tie_break=tie_break,
        warnings=tuple(warnings),
        dropped=dropped,
        producer_efforts=efforts_for(producer, lane_efforts),
    )


def _pick_verifier(
    packet: dict[str, Any],
    rows: Sequence[RegistryRow],
    health: Mapping[str, Health | bool],
    producer: RegistryRow,
) -> tuple[str | None, str | None]:
    pinned = _text(packet, "verifier_lane")
    if pinned is not None:
        row = next((r for r in rows if r.agent_id == pinned), None)
        if row is None or not row.may_verify or not _is_up(health, pinned)[0]:
            return None, f"verifier_lane {pinned} is unknown, down, or may not verify"
        if pinned == producer.agent_id:
            return pinned, "the verifier lane is the producer lane; it runs in a fresh session"
        return pinned, None
    pool = [
        row
        for row in rows
        if row.may_verify and row.agent_id != producer.agent_id and _is_up(health, row.agent_id)[0]
    ]
    pool.sort(key=_producer_key)
    if pool:
        return pool[0].agent_id, None
    if producer.may_verify:
        return (
            producer.agent_id,
            "no independent verifier lane is up; verification runs in a fresh session on "
            f"{producer.agent_id}",
        )
    return None, "no lane that may verify is up"
