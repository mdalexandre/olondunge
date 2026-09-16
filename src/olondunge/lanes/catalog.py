"""The Codex CLI's local model catalog, reshaped into model and effort metadata.

The Codex CLI caches its model list under its own home directory. This module reads that
cache (or refreshes it through `codex debug models`) and returns slugs, visibility and the
reasoning efforts each model accepts. It never returns prompt bodies and never claims to
know account access or billing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

REFRESH_TIMEOUT_S = 60
RECOVERY = "run `codex debug models` to populate the Codex model catalog cache"


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def cache_path() -> Path:
    raw = os.environ.get("OLONDUNGE_CODEX_MODELS_CACHE", "").strip()
    return Path(raw).expanduser() if raw else codex_home() / "models_cache.json"


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "recovery": RECOVERY,
        "source": None,
        "fetched_at": None,
        "client_version": None,
        "model_count": 0,
        "account_access": "UNKNOWN",
        "billing": "UNKNOWN",
        "models": [],
    }


def _levels(raw: dict[str, Any]) -> list[str]:
    source = raw.get("supported_reasoning_levels", raw.get("reasoning_levels", []))
    if not isinstance(source, list):
        return []
    out: list[str] = []
    for level in source:
        if isinstance(level, dict) and isinstance(level.get("effort"), str):
            out.append(level["effort"])
        elif isinstance(level, str):
            out.append(level)
    return out


def _from_payload(data: Any, source: str) -> dict[str, Any]:
    models_raw = data.get("models") if isinstance(data, dict) else data
    if not isinstance(models_raw, list):
        return _blocked(f"catalog at {source} has no models list")
    models: list[dict[str, Any]] = []
    for raw in models_raw:
        if not isinstance(raw, dict) or not isinstance(raw.get("slug"), str):
            continue
        visibility = raw.get("visibility")
        models.append(
            {
                "slug": raw["slug"],
                "display_name": raw.get("display_name"),
                "visibility": visibility,
                "selectable": visibility in (None, "list"),
                "priority": raw.get("priority"),
                "default_reasoning_level": raw.get("default_reasoning_level"),
                "efforts": _levels(raw),
                "context_window": raw.get("context_window"),
            }
        )
    meta = data if isinstance(data, dict) else {}
    return {
        "status": "ok",
        "reason": None,
        "recovery": None,
        "source": source,
        "fetched_at": meta.get("fetched_at"),
        "client_version": meta.get("client_version"),
        "model_count": len(models),
        "account_access": "UNKNOWN",
        "billing": "UNKNOWN",
        "models": models,
    }


def load(refresh: bool = False, path: Path | None = None) -> dict[str, Any]:
    """Load the catalog. Never raises; a missing or malformed catalog returns `blocked`."""

    try:
        if refresh:
            binary = shutil.which("codex")
            if binary is None:
                return _blocked("codex binary was not found on PATH")
            proc = subprocess.run(
                [binary, "debug", "models"],
                capture_output=True,
                text=True,
                timeout=REFRESH_TIMEOUT_S,
                check=False,
            )
            if proc.returncode != 0:
                return _blocked("codex debug models exited nonzero")
            return _from_payload(json.loads(proc.stdout), "codex debug models")
        target = path if path is not None else cache_path()
        return _from_payload(json.loads(target.read_text(encoding="utf-8")), str(target))
    except FileNotFoundError:
        return _blocked(f"no catalog cache at {path if path is not None else cache_path()}")
    except subprocess.TimeoutExpired:
        return _blocked("codex debug models timed out")
    except (OSError, ValueError, RecursionError) as exc:
        return _blocked(f"catalog unreadable: {type(exc).__name__}")


def selectable_efforts(catalog: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for model in catalog.get("models", []):
        if isinstance(model, dict) and model.get("selectable") and model.get("efforts"):
            out[str(model["slug"])] = list(model["efforts"])
    return out
