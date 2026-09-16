"""The tool bodies behind the MCP server, callable in process for tests and the CLI.

Every public method returns a dict with `status` in ok, ok_with_warnings, blocked or failed
and never raises. A blocked result names what is missing and how to recover.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from olondunge import briefs, preflight
from olondunge.jobs import runner, store
from olondunge.jobs.ledger import read_rows
from olondunge.lanes import build_lane, catalog, process
from olondunge.lanes.base import Brief, Health, Lane
from olondunge.paths import home
from olondunge.redact import redact
from olondunge.registry.schema import RegistryRow, load_registry
from olondunge.registry.select import Plan, efforts_for, resolve_effort, select_lane
from olondunge.verify import LeakError, build_verify_packet

STATUSES = frozenset({"ok", "ok_with_warnings", "blocked", "failed"})
NEXT_AFTER_SUBMIT = (
    "call alloc_poll(job_id) until job_status is done, failed or blocked, then "
    "alloc_collect(job_id); or alloc_collect(job_id, wait_s=N) to wait up to N seconds"
)
F = TypeVar("F", bound=Callable[..., dict[str, Any]])


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def result(status: str, **payload: Any) -> dict[str, Any]:
    if status not in STATUSES:
        status = "failed"
    if status == "ok" and payload.get("warnings"):
        status = "ok_with_warnings"
    out: dict[str, Any] = {"status": status}
    out.update(_clean(payload))
    out["status"] = status
    return out


def depth_refusal() -> dict[str, Any] | None:
    """Blocked when this server runs inside a worker at the maximum allocation depth."""

    depth = process.current_depth()
    if depth < process.max_depth():
        return None
    return result(
        "blocked",
        missing="allocation depth",
        reason=f"this server runs inside an Olondunge worker (depth {depth}); nested "
        "allocation is refused so a worker cannot recurse",
        recovery="do the work in this session, or raise OLONDUNGE_MAX_DEPTH in the host",
    )


def _never_raise(method: F) -> F:
    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return method(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a tool call must return, not raise
            return result("failed", error=redact(f"{type(exc).__name__}: {exc}")[:300])

    return wrapper  # type: ignore[return-value]


class Surface:
    def __init__(
        self,
        rows: list[RegistryRow] | None = None,
        lanes: Mapping[str, Lane] | None = None,
        catalog_loader: Callable[[bool], dict[str, Any]] | None = None,
    ) -> None:
        self._rows_override = rows
        self._lanes_override = dict(lanes) if lanes is not None else None
        self._catalog_loader = catalog_loader or (lambda refresh: catalog.load(refresh=refresh))

    # registry and health -------------------------------------------------------------

    def rows(self) -> tuple[list[RegistryRow], str]:
        if self._rows_override is not None:
            return list(self._rows_override), "injected"
        return load_registry()

    def lanes(self, rows: list[RegistryRow]) -> dict[str, Lane]:
        if self._lanes_override is not None:
            return dict(self._lanes_override)
        out: dict[str, Lane] = {}
        for row in rows:
            lane = build_lane(row.agent_id, row.transport)
            if lane is not None:
                out[row.agent_id] = lane
        return out

    def _health(self, rows: list[RegistryRow], lanes: Mapping[str, Lane]) -> dict[str, Health]:
        health: dict[str, Health] = {}
        for row in rows:
            lane = lanes.get(row.agent_id)
            if lane is None:
                health[row.agent_id] = Health(False, f"no implementation for {row.agent_id}", None)
                continue
            try:
                health[row.agent_id] = lane.health()
            except Exception as exc:  # noqa: BLE001 - a broken probe is a down lane
                health[row.agent_id] = Health(False, f"probe failed: {type(exc).__name__}", None)
        return health

    def _lane_efforts(
        self, rows: list[RegistryRow], packet: Mapping[str, Any]
    ) -> dict[str, list[str]]:
        """Codex efforts come from its catalog, per model, when the catalog is readable."""

        out: dict[str, list[str]] = {}
        for row in rows:
            if row.effort_source != "codex_catalog":
                continue
            cat = self._catalog_loader(False)
            if cat.get("status") != "ok":
                continue
            wanted = packet.get("model") if isinstance(packet.get("model"), str) else None
            slug = wanted or preflight.default_codex_model(cat, preflight.codex_config_defaults())
            for entry in cat.get("models") or []:
                if isinstance(entry, dict) and entry.get("slug") == slug and entry.get("efforts"):
                    out[row.agent_id] = [str(e) for e in entry["efforts"]]
        return out

    def _plan(
        self, packet: dict[str, Any], **kwargs: Any
    ) -> tuple[Plan, list[RegistryRow], dict[str, Lane]]:
        rows, _source = self.rows()
        lanes = self.lanes(rows)
        health = self._health(rows, lanes)
        plan = select_lane(
            packet, rows, health, lane_efforts=self._lane_efforts(rows, packet), **kwargs
        )
        return plan, rows, lanes

    # tools ----------------------------------------------------------------------------

    @_never_raise
    def status(self) -> dict[str, Any]:
        rows, source = self.rows()
        lanes = self.lanes(rows)
        health = self._health(rows, lanes)
        probed_at = store.utc_now()
        entries = []
        for row in rows:
            probe = health[row.agent_id]
            entry = row.public()
            entry.update(up=probe.up, reason=probe.reason, binary=probe.binary, probed_at=probed_at)
            entries.append(entry)
        warnings = (
            []
            if any(e["up"] for e in entries)
            else ["no lane is up; install claude, codex or grok, or start a local model server"]
        )
        return result(
            "ok", lanes=entries, registry_source=source, home=str(home()), warnings=warnings
        )

    @_never_raise
    def plan(self, packet: Any) -> dict[str, Any]:
        if not isinstance(packet, dict):
            return result("blocked", missing="packet", recovery="pass a JSON object packet")
        chosen, _rows, _lanes = self._plan(packet)
        payload = chosen.public()
        status = payload.pop("status")
        if status == "blocked":
            payload["recovery"] = (
                "read `dropped` for each lane's reason; change the packet (tools, authority, "
                "model, effort, lane) or bring a lane up"
            )
        return result(status, **payload)

    @_never_raise
    def dispatch(self, packet: Any) -> dict[str, Any]:
        if (refusal := depth_refusal()) is not None:
            return refusal
        if not isinstance(packet, dict):
            return result("blocked", missing="packet", recovery="pass a JSON object packet")
        chosen, _rows, lanes = self._plan(packet)
        if chosen.status == "blocked" or chosen.producer is None:
            payload = chosen.public()
            payload.pop("status")
            return result(
                "blocked",
                missing=chosen.reason,
                recovery="call alloc_plan and read `dropped`",
                **payload,
            )
        try:
            brief = briefs.compile_brief(packet, model=chosen.model, effort=chosen.effort)
        except ValueError as exc:
            return result("blocked", missing=str(exc), recovery="fix the packet field named")
        return self._submit(lanes[chosen.producer], brief, chosen, packet, kind="work")

    def _submit(
        self,
        lane: Lane,
        brief: Brief,
        chosen: Plan,
        packet: Mapping[str, Any],
        *,
        kind: str,
        extra_job_fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = lane.submit(brief)
        fields: dict[str, Any] = {
            "task_id": packet.get("task_id") if isinstance(packet.get("task_id"), str) else None,
            "planned_verifier": chosen.verifier,
            "write_allowed": brief.write_allowed,
        }
        fields.update(extra_job_fields or {})
        store.update_job(job.job_dir, **fields)
        return result(
            "ok",
            job_id=job.value,
            kind=kind,
            producer=chosen.producer,
            verifier=chosen.verifier,
            model=chosen.model,
            effort=chosen.effort,
            cwd=str(brief.cwd),
            write_allowed=brief.write_allowed,
            warnings=list(chosen.warnings),
            next=NEXT_AFTER_SUBMIT,
        )

    @_never_raise
    def poll(self, job_id: Any) -> dict[str, Any]:
        if not isinstance(job_id, str) or not job_id:
            return result("blocked", missing="job_id", recovery="pass the job_id a submit returned")
        return result(**_split_status(runner.poll(job_id)))

    @_never_raise
    def collect(self, job_id: Any, wait_s: Any = 0) -> dict[str, Any]:
        if not isinstance(job_id, str) or not job_id:
            return result("blocked", missing="job_id", recovery="pass the job_id a submit returned")
        wait = float(wait_s) if isinstance(wait_s, (int, float)) else 0.0
        return result(**_split_status(runner.collect(job_id, wait)))

    @_never_raise
    def verify(self, packet: Any, candidate: Any) -> dict[str, Any]:
        if (refusal := depth_refusal()) is not None:
            return refusal
        if not isinstance(packet, dict):
            return result("blocked", missing="packet", recovery="pass the original task packet")
        if not isinstance(candidate, dict):
            return result(
                "blocked",
                missing="candidate",
                recovery="pass {artifact_paths: [...], evidence: [...], job_id?: producer job}",
            )
        producer = candidate.get("producer") if isinstance(candidate.get("producer"), str) else None
        produced_by = candidate.get("job_id")
        if isinstance(produced_by, str):
            job_dir = store.job_dir_for(produced_by)
            if job_dir is not None and (job_dir / "job.json").is_file():
                producer = str(store.load_job(job_dir).get("lane") or producer or "") or None
        try:
            verify_packet = build_verify_packet(packet, candidate)
        except LeakError as exc:
            return result(
                "blocked",
                missing="a blind candidate",
                reason=str(exc),
                recovery="remove the producer's verdict, score or reasoning",
            )
        except ValueError as exc:
            return result("blocked", missing=str(exc), recovery="add the field named")
        rows, _source = self.rows()
        verifiers = [row for row in rows if row.may_verify]
        lanes = self.lanes(rows)
        health = self._health(verifiers, lanes)
        chosen = select_lane(
            verify_packet,
            verifiers,
            health,
            lane_efforts=self._lane_efforts(verifiers, verify_packet),
            exclude_producer=producer,
        )
        if chosen.status == "blocked" or chosen.producer is None:
            payload = chosen.public()
            payload.pop("status")
            return result(
                "blocked", missing=chosen.reason, recovery="bring a verifying lane up", **payload
            )
        try:
            brief = briefs.compile_brief(
                verify_packet, model=chosen.model, effort=chosen.effort, kind="verify"
            )
        except ValueError as exc:
            return result("blocked", missing=str(exc), recovery="fix the packet field named")
        out = self._submit(
            lanes[chosen.producer],
            brief,
            chosen,
            verify_packet,
            kind="verify",
            extra_job_fields={"verifies_job": produced_by, "artifact_producer": producer},
        )
        out["verifier"] = chosen.producer
        out.pop("producer", None)
        out["next"] = NEXT_AFTER_SUBMIT + "; the collected envelope carries `verdict`"
        return out

    @_never_raise
    def call(
        self, worker: Any, brief_file: Any, model: Any = None, effort: Any = None
    ) -> dict[str, Any]:
        if (refusal := depth_refusal()) is not None:
            return refusal
        rows, _source = self.rows()
        lanes = self.lanes(rows)
        row = next((r for r in rows if r.agent_id == worker), None)
        if row is None or worker not in lanes:
            known = ", ".join(sorted(lanes))
            return result("blocked", missing=f"worker {worker}", recovery=f"use one of: {known}")
        health = self._health([row], lanes)[row.agent_id]
        if not health.up:
            return result(
                "blocked",
                missing=f"{worker} is down: {health.reason}",
                recovery="install or log in to that CLI",
            )
        if not isinstance(brief_file, str) or not Path(brief_file).expanduser().is_file():
            return result("blocked", missing="brief_file", recovery="pass an existing file path")
        path = Path(brief_file).expanduser().resolve()
        if path.stat().st_size > process.MAX_PROMPT_BYTES:
            return result(
                "blocked",
                missing="a smaller brief",
                recovery=f"keep the brief under {process.MAX_PROMPT_BYTES} bytes",
            )
        text = path.read_text(encoding="utf-8", errors="replace")
        cleaned = redact(text)
        resolved_effort: str | None = None
        if isinstance(effort, str) and effort.strip():
            supported = efforts_for(row, self._lane_efforts([row], {"model": model}))
            resolved_effort = resolve_effort(effort, supported)
            if resolved_effort is None:
                return result(
                    "blocked",
                    missing=f"effort {effort} for {worker}",
                    recovery=f"valid: {', '.join(supported) or 'none'}",
                )
        brief = Brief(
            prompt=cleaned,
            cwd=briefs.new_scratch_dir("call"),
            prompt_file=path if cleaned == text else None,
            model=model if isinstance(model, str) and model.strip() else None,
            effort=resolved_effort,
            read_dirs=(str(path.parent),),
        )
        chosen = Plan("ok", row.agent_id, None, brief.model, brief.effort, "", row.cost_class)
        warnings = [] if cleaned == text else ["credential shaped text was redacted from the brief"]
        out = self._submit(lanes[row.agent_id], brief, chosen, {}, kind="work")
        if warnings:
            out["warnings"] = warnings
            out["status"] = "ok_with_warnings"
        return out

    @_never_raise
    def models(self, refresh: bool = False) -> dict[str, Any]:
        cat = self._catalog_loader(bool(refresh))
        rows, _source = self.rows()
        lanes_meta = {
            row.agent_id: {
                "efforts": list(row.efforts),
                "models": list(row.models),
                "model_patterns": list(row.model_patterns),
                "default_model": row.default_model,
                "default_effort": row.default_effort,
                "effort_source": row.effort_source or "registry",
            }
            for row in rows
        }
        payload = dict(cat)
        catalog_status = payload.pop("status", "blocked")
        payload["codex_catalog_status"] = catalog_status
        payload["lanes"] = lanes_meta
        payload["portable_efforts"] = {
            "top": "the lane's strongest effort",
            "max": "the lane's own max when it has one, else its strongest",
        }
        warnings = [] if catalog_status == "ok" else [f"codex catalog: {cat.get('reason')}"]
        return result("ok", warnings=warnings, **payload)

    @_never_raise
    def preflight(
        self, runtime: str = "codex", roles: Any = None, refresh: bool = False
    ) -> dict[str, Any]:
        rows, _source = self.rows()
        lanes_status = self.status()
        cat = self._catalog_loader(bool(refresh)) if str(runtime).lower() == "codex" else None
        return preflight.build_preflight(
            runtime=runtime,
            roles=roles,
            refresh=refresh,
            rows=rows,
            lanes=lanes_status.get("lanes") or [],
            catalog=cat,
        )

    def ledger_text(self) -> str:
        try:
            return redact("\n".join(json.dumps(row, sort_keys=True) for row in read_rows()))
        except Exception:  # noqa: BLE001 - a resource read returns empty, never raises
            return ""


def _split_status(data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data)
    payload["status"] = payload.get("status", "failed")
    return payload
