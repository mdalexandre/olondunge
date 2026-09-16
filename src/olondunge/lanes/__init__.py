"""Lane implementations, and the parser each lane's output goes through."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from olondunge.lanes import claude, codex, grok, local
from olondunge.lanes.base import Brief, Envelope, Health, JobId, Lane

Parser = Callable[[Path, str, str], tuple[str, dict[str, Any] | None, list[str]]]

PARSERS: dict[str, Parser] = {
    "claude": claude.parse,
    "codex": codex.parse,
    "grok": grok.parse,
    "local": local.parse,
}


def build_lane(agent_id: str, transport: str) -> Lane | None:
    """The lane object for a registry row, or None when no implementation exists."""

    if agent_id == "claude":
        return claude.ClaudeLane()
    if agent_id == "codex":
        return codex.CodexLane()
    if agent_id == "grok":
        return grok.GrokLane()
    if agent_id == "local" and transport == "http":
        try:
            return local.LocalLane()
        except ValueError:
            return None
    return None


__all__ = ["Brief", "Envelope", "Health", "JobId", "Lane", "PARSERS", "build_lane"]
