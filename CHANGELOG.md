# Changelog

## Unreleased

- Visual identity in `assets/`: the mark (glowing, flat, animated and favicon variants), a
  wordmark for a dark and for a light background, rasterised icons, a 1280 by 640 GitHub social
  preview, a README banner and two hero scenes.
- `assets/build.py` renders every PNG from its committed SVG through headless Chrome and asserts
  each output's dimensions, byte size and background (transparent or opaque), with no network
  call and no dependency outside the standard library. `OLONDUNGE_CHROME` names the browser binary when it is not on PATH.
- `assets/BRAND.md` carries the palette, the geometry, clear space, minimum sizes and the usage
  rules.

## 0.1.0 (2026-09-16)

First public release.

- MCP server with `alloc_status`, `alloc_plan`, `alloc_dispatch`, `alloc_poll`, `alloc_collect`,
  `alloc_verify`, `alloc_call`, `tri_models`, `tri_preflight` and the `alloc://ledger` resource.
- Lanes for Claude Code, Codex CLI, Grok CLI and a loopback OpenAI compatible endpoint, each
  taking a model and a reasoning effort with that CLI's own flags.
- Self-allocation fields `lane`, `model`, `effort` and `verifier_lane`, with per lane effort
  validation, portable `top` and `max`, and a `dropped` reason for every lane not chosen.
- Blind verification jobs with a parsed `VERDICT` line, the original inputs kept, and a leak
  guard against producer conclusions.
- Background jobs that survive a server restart, non-blocking collect, timeouts, and redaction.
- The `tristack-compiler` and `olondunge` skills; the tri-stack contract in Claude Code (1.1-cc),
  Codex (1.1-cx) and Grok (1.1-gk) editions; `tri:` hooks for Claude Code and Codex and the
  `tri-grok` entry point.
- `olondunge setup`, `olondunge doctor`, and plugin manifests for Claude Code, Codex and Grok.
- An allocation depth guard, so a worker that loads Olondunge cannot dispatch further jobs.
- Live tested on Claude Code, Codex CLI 0.154.0 and Grok CLI 1.0.30: dispatch, collect,
  deny enforcement, blind verification, `tri:` activation on each host, `olondunge setup all`,
  and plugin installs.
