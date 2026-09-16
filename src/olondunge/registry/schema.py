"""Registry rows: which lanes exist, what they may do, and what they cost."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from olondunge.paths import registry_override_path

COST_RANK: dict[str, int] = {
    "local_gpu": 0,
    "subscription": 1,
    "anthropic_plan": 2,
    "orchestrator_tokens": 3,
}
TRANSPORTS = frozenset({"subprocess", "http"})


def _strings(d: dict[str, Any], key: str, required: bool = True) -> tuple[str, ...]:
    if key not in d:
        if required:
            raise ValueError(f"registry row is missing field {key}")
        return ()
    value = d[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"registry field {key} must be a list of strings")
    return tuple(value)


@dataclass(frozen=True)
class RegistryRow:
    agent_id: str
    role: str
    capabilities: frozenset[str]
    may_verify: bool
    permitted_tools: tuple[str, ...]
    cost_class: str
    transport: str
    context_budget_tokens: int
    authority_ceiling: frozenset[str]
    preferred_index: int
    efforts: tuple[str, ...]
    models: tuple[str, ...]
    model_patterns: tuple[str, ...]
    effort_source: str | None
    default_model: str | None
    default_effort: str | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RegistryRow:
        if not isinstance(d, dict):
            raise ValueError("registry row must be an object")
        agent_id = d.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("registry field agent_id must be a nonempty string")
        if d.get("cost_class") not in COST_RANK:
            raise ValueError(
                f"registry row {agent_id}: cost_class must be one of {sorted(COST_RANK)}"
            )
        if d.get("transport") not in TRANSPORTS:
            raise ValueError(f"registry row {agent_id}: transport must be subprocess or http")
        if not isinstance(d.get("may_verify"), bool):
            raise ValueError(f"registry row {agent_id}: may_verify must be a boolean")
        budget = d.get("context_budget_tokens")
        if type(budget) is not int or budget <= 0:
            raise ValueError(f"registry row {agent_id}: context_budget_tokens must be positive")
        preferred = d.get("preferred_index", 0)
        if type(preferred) is not int:
            raise ValueError(f"registry row {agent_id}: preferred_index must be an integer")
        for key in ("default_model", "default_effort", "effort_source"):
            if d.get(key) is not None and not isinstance(d.get(key), str):
                raise ValueError(f"registry row {agent_id}: {key} must be a string or null")
        return cls(
            agent_id=agent_id,
            role=str(d.get("role") or agent_id),
            capabilities=frozenset(_strings(d, "capabilities")),
            may_verify=d["may_verify"],
            permitted_tools=_strings(d, "permitted_tools"),
            cost_class=d["cost_class"],
            transport=d["transport"],
            context_budget_tokens=budget,
            authority_ceiling=frozenset(_strings(d, "authority_ceiling")),
            preferred_index=preferred,
            efforts=_strings(d, "efforts", required=False),
            models=_strings(d, "models", required=False),
            model_patterns=_strings(d, "model_patterns", required=False),
            effort_source=d.get("effort_source"),
            default_model=d.get("default_model"),
            default_effort=d.get("default_effort"),
        )

    def public(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "capabilities": sorted(self.capabilities),
            "may_verify": self.may_verify,
            "permitted_tools": list(self.permitted_tools),
            "cost_class": self.cost_class,
            "authority_ceiling": sorted(self.authority_ceiling),
            "efforts": list(self.efforts),
            "models": list(self.models),
            "default_model": self.default_model,
            "default_effort": self.default_effort,
        }


def parse_rows(raw: Any) -> list[RegistryRow]:
    if not isinstance(raw, list):
        raise ValueError("the registry must be a JSON list of rows")
    rows: list[RegistryRow] = []
    seen: set[str] = set()
    for item in raw:
        row = RegistryRow.from_dict(item)
        if row.agent_id in seen:
            raise ValueError(f"duplicate agent_id {row.agent_id}")
        seen.add(row.agent_id)
        rows.append(row)
    return rows


def default_rows() -> list[RegistryRow]:
    text = (
        resources.files("olondunge.registry")
        .joinpath("default_registry.json")
        .read_text(encoding="utf-8")
    )
    return parse_rows(json.loads(text))


def load_registry(path: Path | None = None) -> tuple[list[RegistryRow], str]:
    """Rows plus their source. A user file at OLONDUNGE_REGISTRY or ~/.olondunge/registry.json
    replaces the packaged default entirely."""

    target = path if path is not None else registry_override_path()
    if target.is_file():
        return parse_rows(json.loads(target.read_text(encoding="utf-8"))), str(target)
    return default_rows(), "packaged default"
