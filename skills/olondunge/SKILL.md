---
name: olondunge
description: Allocate work to the agent CLIs on this machine (claude, codex, grok, a local model) through the Olondunge MCP server, choosing the lane, the model and the reasoning effort yourself, then verify the result blind on a different lane. Use when a task should run on another model or CLI, needs a second model's independent check, needs a stronger or cheaper model or effort than the current session, or when the user mentions Olondunge, alloc_dispatch, alloc_plan, alloc_verify, lanes, or model and effort allocation.
---

# Olondunge: allocate yourself across models

Olondunge is an MCP server. Its tools let you, the model in this session, hand a bounded
piece of work to another agent CLI on this machine and pick which one, on which model, at
which reasoning effort. The server only applies your decision against what is installed
and healthy, and tells you why it dropped every lane it did not choose.

The tools appear as `alloc_*` and `tri_*` under the server name `olondunge` (in Claude
Code `mcp__olondunge__alloc_status`, or `mcp__plugin_olondunge_olondunge__alloc_status`
when installed as a plugin). If none of them are in your tool list, the server is not
registered: tell the user to run `olondunge setup <claude|codex|grok>` and restart.

## The loop

1. `alloc_status()` once per task: which lanes are `up`, and each lane's `efforts`,
   `models`, `cost_class`, `capabilities` and `may_verify`.
2. Decide what the work needs. Write a packet (below). Pin `lane`, `model` and `effort`
   only when you have a reason; otherwise let the server pick the cheapest capable lane.
3. `alloc_plan(packet)`: a dry run. Read `producer`, `model`, `effort`, `verifier`,
   `warnings` and `dropped`. A `blocked` plan names each lane's reason in `dropped`; fix the
   packet vocabulary, never by removing a prohibition or an acceptance criterion.
4. `alloc_dispatch(packet)` returns a `job_id` immediately. The job runs in the background.
5. `alloc_poll(job_id)` until `job_status` is `done`, `failed` or `blocked`, or
   `alloc_collect(job_id, wait_s=120)` to wait. `alloc_collect` returns the envelope:
   `status`, `reply` (4000 characters; the full text is `reply.txt` in `job_dir`),
   `summary`, `defects`, `usage`, `model`, `effort`.
6. For anything the user will rely on: `alloc_verify(packet, candidate)` with
   `candidate = {"artifact_paths": [...], "evidence": [...], "job_id": "<producer job>"}`.
   It starts a verify job on a lane other than the producer. Collect it; its envelope carries
   `verdict`: `PASS`, `FAIL` or `BLOCKED`. Only `PASS` counts as verified.

Never put your own conclusion, score, confidence or reasoning in the candidate: the
verifier must reach its verdict blind, and `alloc_verify` refuses a candidate that carries
them. When only one lane can verify, the check runs in a fresh session on the producer
lane and the result says so in `warnings`; report that as weaker than independent.

## The packet

```json
{
  "task_id": "short-slug",
  "objective": "What the worker must do, in one or two sentences.",
  "inputs": ["/absolute/path/to/file-or-dir"],
  "expected_output": "What the reply must contain.",
  "acceptance_criteria": ["Checkable statement 1", "Checkable statement 2"],
  "permitted_tools": ["Read", "Grep", "Glob"],
  "authority_ceiling": ["read-local-filesystem"],
  "prohibited_actions": ["deploying", "reading credentials"],
  "stop_conditions": ["the expected output is complete"],
  "timeout_s": 900,
  "tool_call_limit": 40,

  "lane": "codex",
  "model": "gpt-5.5",
  "effort": "high",
  "verifier_lane": "claude"
}
```

The last four fields are optional self-allocation. Vocabulary the filter understands (an
unknown word drops every lane):

- `permitted_tools`: `Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebSearch`,
  `Sandbox`, `http`.
- `authority_ceiling`: `read-local-filesystem`, `write-artifact-namespace`,
  `write-workdir`, `local-http`.
- `capabilities` (optional; inferred from `expected_output` when absent): `drafting`,
  `code`, `inspection`, `research`, `independent_verification`, `triage`, `extract`.
- `effort`: a value the lane lists in `alloc_status`, or the portable `top` (the lane's
  strongest) or `max` (the lane's own max, else its strongest). `tri_models` lists the
  Codex efforts per model.

Writing: a worker writes only in its own scratch directory under `~/.olondunge/scratch/`.
To let it change a project, add `"workdir": "/absolute/project/path"`, put
`write-workdir` in `authority_ceiling`, and permit `Write`, `Edit` or `Bash`. Deny rules
the packet cannot lift keep every worker away from privilege escalation, recursive
deletes, pushes, history rewrites, containers and service managers.

## Choosing lane, model and effort

- Cheap, bounded, well specified work: the default selection, or a middle effort.
- Hard reasoning, architecture, subtle bugs: pin a stronger model or `effort: "top"`.
- A second opinion: `verifier_lane` on a different vendor than the producer.
- Web research: permit `WebSearch` and pick a lane whose `permitted_tools` include it.
- Local model lane (`local`): triage and extraction only; it never verifies.

## Other tools

- `alloc_call(worker, brief_file, model, effort)`: run an existing brief file on a named
  lane, read only, skipping selection.
- `tri_models(refresh)`: the Codex model catalog and each lane's efforts.
- `tri_preflight(runtime, roles)`: filled `runtime_preflight` blocks for a tri-stack
  contract (see the `tristack-compiler` skill).
- Resource `alloc://ledger`: one row per finished job (lane, model, effort, outcome,
  verdict, tokens).

## Honesty rules

- A dispatched job is not a finished job; a finished job is not a verified one.
- Report the lane, model, effort and `job_id` for every result you use.
- `failed` or `blocked` envelopes are reported as such, with their `defects`.
