"""The Olondunge MCP server (stdio).

The host model (Claude Code, Codex or Grok) decides what a piece of work needs; these tools
run that decision on the agent CLIs installed on this machine: which lane, which model,
which reasoning effort, and which other lane verifies the result blind.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from olondunge import __version__
from olondunge.surface import Surface

INSTRUCTIONS = """\
Olondunge lets you allocate work to the agent CLIs on this machine (claude, codex, grok, and
a local model endpoint), choosing the lane, model and reasoning effort yourself.

Flow: alloc_status (what is up, which models and efforts each lane takes) -> alloc_plan(packet)
(dry run: producer, verifier, model, effort, and why every other lane was dropped) ->
alloc_dispatch(packet) -> alloc_poll(job_id) -> alloc_collect(job_id) -> alloc_verify(packet,
candidate) for a blind check by a different lane -> alloc_collect on that job for its verdict.

A packet is a JSON object: objective (required), task_id, inputs (paths), expected_output,
acceptance_criteria, permitted_tools (Read, Write, Edit, Bash, Glob, Grep, WebSearch, Sandbox),
authority_ceiling (read-local-filesystem, write-artifact-namespace, write-workdir),
prohibited_actions, stop_conditions, context, timeout_s, tool_call_limit, workdir. Optional
self-allocation: lane, model, effort (low, medium, high, xhigh, max, or portable top),
verifier_lane. Workers write only in their own scratch directory, or in workdir when the
authority ceiling carries write-workdir. Jobs run in the background; nothing blocks.
"""

mcp = MCPServer(name="olondunge", version=__version__, instructions=INSTRUCTIONS)
_surface: Surface | None = None


def surface() -> Surface:
    global _surface
    if _surface is None:
        _surface = Surface()
    return _surface


@mcp.tool()
def alloc_status() -> dict[str, Any]:
    """Probe every lane. Each entry: agent_id, up, reason, cost_class, capabilities, may_verify,
    permitted_tools, authority_ceiling, efforts, models, default_model, default_effort."""
    return surface().status()


@mcp.tool()
def alloc_plan(packet: dict[str, Any]) -> dict[str, Any]:
    """Dry run the allocation for a packet without starting anything. Returns producer,
    verifier, model, effort, producer_efforts, warnings, and `dropped` (why each other lane was
    not chosen). Pin a lane, model or effort in the packet to allocate them yourself; an effort
    the lane cannot take is refused with the valid values."""
    return surface().plan(packet)


@mcp.tool()
def alloc_dispatch(packet: dict[str, Any]) -> dict[str, Any]:
    """Plan, compile a brief, and start the job in the background. Returns job_id, producer,
    verifier, model, effort, cwd and write_allowed. Follow with alloc_poll and alloc_collect."""
    return surface().dispatch(packet)


@mcp.tool()
def alloc_poll(job_id: str) -> dict[str, Any]:
    """Current state of a job without waiting: job_status is queued, running, done, failed or
    blocked. Never returns the transcript."""
    return surface().poll(job_id)


@mcp.tool()
def alloc_collect(job_id: str, wait_s: float = 0) -> dict[str, Any]:
    """The finished job's envelope: status, reply (capped at 4000 characters, full text in
    reply.txt under job_dir), summary, defects, usage, model, effort, and verdict for a verify
    job. Returns blocked while the job runs; wait_s (at most 600) waits for it first."""
    return surface().collect(job_id, wait_s)


@mcp.tool()
def alloc_verify(packet: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Start a blind verification job on a lane other than the producer (the producer lane in a
    fresh session only when nothing else can verify). packet is the original task packet with
    acceptance_criteria; candidate is {artifact_paths, evidence, job_id (the producer job),
    verifier_model, verifier_effort}. A candidate carrying a verdict, conclusion, score,
    confidence or reasoning is refused. Collect the returned job for `verdict`."""
    return surface().verify(packet, candidate)


@mcp.tool()
def alloc_call(
    worker: str, brief_file: str, model: str | None = None, effort: str | None = None
) -> dict[str, Any]:
    """Escape hatch: run an existing brief file on a named lane with an optional model and
    effort, read only, skipping selection. Returns job_id."""
    return surface().call(worker, brief_file, model, effort)


@mcp.tool()
def tri_models(refresh: bool = False) -> dict[str, Any]:
    """Model and effort metadata: the Codex CLI's local catalog (slugs, visibility, efforts per
    model; refresh=True reruns `codex debug models`) and each lane's efforts and model patterns.
    Account access and billing stay UNKNOWN."""
    return surface().models(refresh)


@mcp.tool()
def tri_preflight(
    runtime: str = "codex", roles: dict[str, Any] | None = None, refresh: bool = False
) -> dict[str, Any]:
    """Filled runtime_preflight blocks for a tri-stack contract on runtime codex, claude or
    grok: worker, lead and specialist as exact model and effort pairs checked against the lane,
    plus external_lanes and tic_lane. roles overrides a tier, e.g. {"worker": {"model": "...",
    "effort": "medium"}}; sonnet, opus and fable are accepted as aliases."""
    return surface().preflight(runtime, roles, refresh)


@mcp.resource(
    "alloc://ledger",
    name="ledger",
    description="One JSON row per finished job: lane, model, effort, outcome, verdict, tokens.",
    mime_type="application/jsonl",
)
def ledger() -> str:
    return surface().ledger_text()


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
