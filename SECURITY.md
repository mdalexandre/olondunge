# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/mdalexandre/olondunge/security/advisories/new)
for this repository. Do not open a public issue for a security problem. Include the version or
commit, the host (Claude Code, Codex or Grok), and the smallest reproduction you can share
without real credentials.

## What Olondunge does with your machine

- It starts the agent CLIs you already have (`claude`, `codex`, `grok`) as background
  processes with your own logins. It never reads, copies, or stores their credentials. Codex
  workers run with a scratch `CODEX_HOME` whose `auth.json` is a symlink to yours.
- Workers are read only unless a packet permits a write tool. Writes are limited to the job's
  scratch directory, or to an explicit `workdir` when the packet's authority ceiling carries
  `write-workdir`. Codex runs in its `read-only` or `workspace-write` sandbox accordingly.
- Deny rules that a packet cannot lift are passed to Claude (`--disallowedTools`) and Grok
  (`--deny`) for privilege escalation, recursive deletes, pushes, hard resets, containers,
  service managers, shutdown and disk tools. Deny rules are a guard rail, not a sandbox: treat
  a write capable job as able to change its working directory.
- Claude workers get no subagent tools, so a job cannot spend your plan on work nobody planned.
- Grok workers load your Grok configuration, including its MCP servers and instruction files,
  because the Grok CLI offers no switch to skip them. Keep that in mind before permitting a
  write tool on the `grok` lane.
- A server running inside a worker refuses to dispatch further jobs (`OLONDUNGE_DEPTH`,
  `OLONDUNGE_MAX_DEPTH`), so a worker that loads Olondunge cannot recurse.
- The `tri:` hooks only compile text you typed. Codex runs them only after you trust them in
  its hook review; Olondunge never writes that trust for you.
- Transcripts, replies, envelopes and ledger rows are redacted for credential shaped text before
  they are written or returned. The redactor errs toward hiding: a plain value after
  `password:` is removed even when it is a type name such as `SecretStr`, and a long unbroken
  value in a `NAME_TOKEN=value` line is removed even without digits. Redaction is a guard rail,
  not a guarantee; keep real secrets out of packets.
- The local lane only talks to loopback hosts and refuses redirects.
- `olondunge setup` backs up every file it writes itself (the Claude Code `settings.json`, the
  Codex `hooks.json`, and any skill it replaces) under `~/.olondunge/backups/<timestamp>/`, and
  never edits a file that does not parse. Server registration runs through each host's own
  `mcp add`, which edits that host's config; those edits are the host CLI's, not backed up here,
  and `olondunge doctor` reports what each host now has.

## Supported versions

Security fixes land on the latest release.
