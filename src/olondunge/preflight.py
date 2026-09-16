"""Runtime preflight for a tri-stack contract, for any of the three hosts.

A contract makes its session write a `runtime_preflight` YAML before any work. This module
fills the parts a server can observe: three role tiers (worker, lead, specialist) as exact
model and effort pairs checked against what the lane accepts, the external lanes, and the
optional TIC lane. Fields only the calling session can observe stay null and are named in
`session_fills`. Account access and billing are never reported as known.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from olondunge.lanes import catalog as catalog_mod
from olondunge.registry.schema import RegistryRow
from olondunge.registry.select import resolve_effort

RUNTIMES = ("codex", "claude", "grok")
ROLES = ("worker", "lead", "specialist")
CONTRACT_KEY_ALIASES = {"sonnet": "worker", "opus": "lead", "fable": "specialist"}
SESSION_FILLS = (
    "session_model",
    "permission_mode",
    "deterministic_tools",
    "concurrency_slots",
    "budget",
    "permission_limits",
    "run_dir",
)
# Role defaults per host. `top` resolves to the lane's strongest effort. A null model
# means the CLI's own default model.
ROLE_DEFAULTS: dict[str, dict[str, dict[str, str | None]]] = {
    "claude": {
        "worker": {"model": "sonnet", "effort": "medium"},
        "lead": {"model": "opus", "effort": "high"},
        "specialist": {"model": "opus", "effort": "top"},
    },
    "codex": {
        "worker": {"model": None, "effort": "medium"},
        "lead": {"model": None, "effort": "high"},
        "specialist": {"model": None, "effort": "top"},
    },
    "grok": {
        "worker": {"model": None, "effort": "medium"},
        "lead": {"model": None, "effort": "high"},
        "specialist": {"model": None, "effort": "top"},
    },
}
CONFIG_KEYS = ("model", "model_reasoning_effort", "sandbox_mode", "approval_policy")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _blocked(reason: str, runtime: str | None = None) -> dict[str, Any]:
    return {"status": "blocked", "reason": reason, "runtime": runtime, "runtime_preflight": None}


def codex_config_defaults(path: Path | None = None) -> dict[str, Any]:
    target = path if path is not None else catalog_mod.codex_home() / "config.toml"
    out: dict[str, Any] = {"source": str(target), "note": "defaults only; a session may override"}
    try:
        with target.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError) as exc:
        out["error"] = f"config unreadable: {type(exc).__name__}"
        data = {}
    for key in CONFIG_KEYS:
        value = data.get(key)
        out[key] = value if isinstance(value, str) else None
    return out


def normalize_roles(roles: Any, runtime: str) -> dict[str, dict[str, str | None]]:
    selections = {role: dict(pick) for role, pick in ROLE_DEFAULTS[runtime].items()}
    if roles is None:
        return selections
    if not isinstance(roles, Mapping):
        raise ValueError("roles must be an object keyed by worker, lead or specialist")
    seen: dict[str, str] = {}
    for raw_key, value in roles.items():
        key = str(raw_key).strip().lower()
        role = CONTRACT_KEY_ALIASES.get(key, key)
        if role not in ROLES:
            raise ValueError(f"unknown role {raw_key!r}; use worker, lead or specialist")
        if role in seen:
            raise ValueError(f"roles {seen[role]!r} and {raw_key!r} both set {role}")
        seen[role] = str(raw_key)
        if not isinstance(value, Mapping) or not set(value) <= {"model", "effort"} or not value:
            raise ValueError(f"role {raw_key!r} takes a model and/or an effort")
        for field in ("model", "effort"):
            if field in value and value[field] is not None and not isinstance(value[field], str):
                raise ValueError(f"role {raw_key!r} field {field} must be a string or null")
        selections[role].update({k: value[k] for k in value})
    return selections


def _catalog_model(cat: Mapping[str, Any], slug: str | None) -> dict[str, Any] | None:
    entries = cat.get("models") if isinstance(cat.get("models"), list) else []
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("slug") == slug:
            return entry
    return None


def _priority(entry: Mapping[str, Any]) -> int:
    value = entry.get("priority")
    return value if isinstance(value, int) else 10**6


def default_codex_model(cat: Mapping[str, Any], config: Mapping[str, Any]) -> str | None:
    configured = config.get("model")
    entry = _catalog_model(cat, configured if isinstance(configured, str) else None)
    if entry is not None and entry.get("selectable"):
        return str(entry["slug"])
    entries = [e for e in (cat.get("models") or []) if isinstance(e, dict) and e.get("selectable")]
    entries.sort(key=_priority)
    return str(entries[0]["slug"]) if entries else None


def _role_block(
    runtime: str,
    selection: Mapping[str, str | None],
    efforts: Sequence[str],
    lane: Mapping[str, Any] | None,
    evidence_prefix: list[str],
) -> dict[str, Any]:
    model, effort = selection.get("model"), selection.get("effort")
    evidence = list(evidence_prefix)
    block: dict[str, Any] = {
        "status": "UNKNOWN",
        "exact_model_or_worker_id": None,
        "reasoning_effort": None,
        "candidate": {"model": model, "effort": effort},
        "dispatch_path": None,
        "evidence": evidence,
    }
    resolved = resolve_effort(effort, efforts) if isinstance(effort, str) else None
    if isinstance(effort, str) and resolved is None:
        block["status"] = "UNAVAILABLE"
        evidence.append(
            f"effort {effort} is not accepted; valid: {', '.join(efforts) or 'none listed'}"
        )
        return block
    if lane is None or lane.get("up") is not True:
        reason = lane.get("reason") if lane is not None else "lane not registered"
        evidence.append(f"the {runtime} lane is not up ({reason})")
        block["status"] = "UNAVAILABLE"
        return block
    block["status"] = "AVAILABLE"
    block["exact_model_or_worker_id"] = model or f"{runtime} CLI default model"
    block["reasoning_effort"] = resolved
    block["dispatch_path"] = (
        f"alloc_dispatch with lane {runtime}"
        + (f", model {model}" if model else "")
        + (f", effort {resolved}" if resolved else "")
    )
    evidence.append(f"the {runtime} lane is up ({lane.get('reason')})")
    evidence.append("account access and billing: UNKNOWN")
    return block


def _lanes_block(lanes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    up = [str(lane.get("agent_id")) for lane in lanes if lane.get("up") is True]
    evidence = [f"{len(up)} of {len(lanes)} lanes up"]
    evidence += [
        f"{lane.get('agent_id')} down ({lane.get('reason')})"
        for lane in lanes
        if lane.get("up") is not True
    ]
    return {"status": "AVAILABLE" if up else "UNAVAILABLE", "lanes_up": up, "evidence": evidence}


def build_preflight(
    *,
    runtime: str,
    roles: Any,
    refresh: bool,
    rows: Sequence[RegistryRow],
    lanes: Sequence[Mapping[str, Any]],
    catalog: Mapping[str, Any] | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Never raises. `lanes` is the alloc_status lane list; tests inject `catalog`."""

    try:
        name = str(runtime).strip().lower()
        if name not in RUNTIMES:
            return _blocked(f"runtime must be one of {', '.join(RUNTIMES)}, not {runtime!r}")
        try:
            selections = normalize_roles(roles, name)
        except ValueError as exc:
            return _blocked(str(exc), name)
        row = next((r for r in rows if r.agent_id == name), None)
        lane = next((entry for entry in lanes if entry.get("agent_id") == name), None)
        extra: dict[str, Any] = {}
        blocks: dict[str, Any] = {}
        if name == "codex":
            cat = catalog if catalog is not None else catalog_mod.load(refresh=refresh)
            config = codex_config_defaults(config_path)
            extra["config_defaults"] = config
            extra["catalog"] = {
                k: cat.get(k) for k in ("status", "source", "fetched_at", "model_count", "reason")
            }
            default_model = default_codex_model(cat, config) if cat.get("status") == "ok" else None
            for role in ROLES:
                pick = dict(selections[role])
                prefix: list[str] = []
                if cat.get("status") != "ok":
                    prefix.append(f"codex catalog {cat.get('status')}: {cat.get('reason')}")
                    efforts: Sequence[str] = row.efforts if row else ()
                else:
                    if pick.get("model") is None:
                        pick["model"] = default_model
                    entry = _catalog_model(cat, pick.get("model"))
                    if entry is None or not entry.get("selectable"):
                        blocks[role] = {
                            "status": "UNAVAILABLE",
                            "exact_model_or_worker_id": None,
                            "reasoning_effort": None,
                            "candidate": pick,
                            "dispatch_path": None,
                            "evidence": [
                                f"the codex catalog does not list {pick.get('model')} as selectable"
                            ],
                        }
                        continue
                    efforts = [str(e) for e in entry.get("efforts") or []]
                    prefix.append(
                        f"codex catalog lists {pick['model']} with efforts {', '.join(efforts)}"
                    )
                blocks[role] = _role_block(name, pick, efforts, lane, prefix)
        else:
            efforts = row.efforts if row else ()
            note = (
                [
                    "in a Claude Code session the Agent tool model list is authoritative for its "
                    "own tiers; this block describes the claude lane"
                ]
                if name == "claude"
                else ["the grok CLI keeps no local model catalog; a null model is its default"]
            )
            for role in ROLES:
                blocks[role] = _role_block(name, selections[role], efforts, lane, list(note))
        preflight: dict[str, Any] = {
            "session_model": None,
            "permission_mode": None,
            "deterministic_tools": None,
            **blocks,
            "external_lanes": _lanes_block(lanes),
            "tic_lane": {
                "status": "NOT_PROBED",
                "binary": None,
                "evidence": [
                    "TIC is optional and not bundled with Olondunge; plain tic on PATH is "
                    "usually the ncurses terminfo compiler"
                ],
            },
            "concurrency_slots": {"observed_total": None, "observed_free": None},
            "budget": {"authorized": None, "observed_remaining": None},
            "permission_limits": [],
            "run_dir": None,
            "unresolved_unknowns": ["account access and billing for every model"],
        }
        warnings = [
            f"{role} is {blocks[role]['status']}: {blocks[role]['evidence'][-1]}"
            for role in ROLES
            if blocks[role]["status"] != "AVAILABLE"
        ]
        return {
            "status": "ok_with_warnings" if warnings else "ok",
            "reason": None,
            "runtime": name,
            "observed_at": _now(),
            "runtime_preflight": preflight,
            "session_fills": list(SESSION_FILLS),
            "contract_key_aliases": dict(CONTRACT_KEY_ALIASES),
            "account_access": "UNKNOWN",
            "billing": "UNKNOWN",
            "warnings": warnings,
            "instructions": (
                "Copy the worker, lead, specialist, external_lanes and tic_lane blocks as "
                "returned, then fill the session_fills keys from this session. A contract "
                "whose YAML names sonnet, opus and fable takes the worker, lead and "
                "specialist blocks under those keys."
            ),
            **extra,
        }
    except Exception as exc:  # noqa: BLE001 - the tool contract is never raise
        return _blocked(f"preflight failed unexpectedly: {type(exc).__name__}")


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}
