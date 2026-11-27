# Changelog

## 0.16.0 — native macOS shell

- SwiftUI macOS app (`desktop/`) driving the `dt` CLI: Today brief, run list
  with approve/reject/undo, and memory search.
- Builds and ad-hoc signs a `.app` bundle via `make app`.
- `DAYTHREAD_BIN` and `DAYTHREAD_HOME` are inherited for dev/custom installs.
- 221 unit tests; Swift shell verified with `swift build` and codesign.

## 0.15.0 — OS sandbox, release artifacts, and dashboard

- macOS `sandbox-exec` backend for isolated tools: file-write allowlist and
  network denial for `shell.run`; configurable via `sandbox.backend`.
- Release pipeline: wheel build, SHA256SUMS, CycloneDX-style SBOM, and optional
  HMAC-signed release manifest (`make release`).
- Subprocess coverage via `COVERAGE_PROCESS_START` + parallel data + combine.
- Deployment assets: Docker Compose (API + relay), nginx TLS template, systemd
  units, and operations README.
- Local web dashboard: `dt ui` / `GET /ui`, CSP-protected, approve/reject/undo,
  briefs, runs, and memory search.
- 221 unit tests.

## 0.14.0 — incremental sync cursors

- Schema v7: persistent `connector_state` per connector.
- Google Calendar uses `syncToken` with `410` full-resync fallback.
- Microsoft Graph Calendar/Mail use `deltaLink` cursors, skip `@removed`
  entries, and fall back to a full delta on resync-required.
- Connector sync persists cursor changes; `dt connector state|reset` and
  `dt connector sync --reset`.
- 213 unit tests.

## 0.13.0 — signed webhooks and MCP resources

- Schema v6: `webhook_events` with dedupe and timestamp indexes.
- Signed webhook ingestion: HMAC-SHA256, timestamp replay window, per-source
  secrets, rate limits, dedupe, and signal/proactivity integration.
- Server `POST /v1/webhooks/{source}` and CLI `dt webhook secret|sign|events`.
- MCP resources: `daythread://today` and memory facts; sensitive resources are
  blocked. MCP prompts: morning brief and plan proposal.
- MCP client helpers for resources and prompts.
- 209 unit tests.

## 0.12.0 — Feishu and Lark connectors

- Feishu and Lark OAuth providers (accounts + open API domains).
- Feishu/Lark Calendar: read events, create events.
- Feishu/Lark Tasks: read tasks (synced into local tasks), create tasks.
- Connector sync now supports `task` items and preserves external IDs.
- 204 unit tests.

## 0.11.0 — model streaming, retries, and egress policy

- Model gateway: retry with exponential backoff, fallback chain, and usage
  accounting.
- Streaming: Echo and OpenAI-compatible SSE streams; `dt ask --stream`.
- Egress policy: `model.egress=local_only` blocks non-local providers.
- Tests for SSE parsing, retries, egress, and CLI streaming.
- 197 unit tests.

## 0.10.0 — subprocess isolation and coverage gate

- Isolated tool runner (`daythread.tool_runner`): external, connector, MCP, and
  shell tools execute in a subprocess with a process-group kill on timeout.
- Server worker threads now get a hard timeout for those tools, not just the CLI
  main thread.
- Mock connector action for isolated execution tests.
- Coverage configuration with a 75% gate; CI covers the matrix with coverage.
- 192 unit tests, ~77% line coverage with current subprocess measurement.

## 0.9.0 — DSAR, test matrix, and packaging

- Privacy/DSAR: `privacy.export_subject`, soft/hard forget, tombstone purge,
  and `dt privacy export|forget|purge`.
- Migration matrix tests from v1 → v5; empty DB, v4 plaintext cache purge, and
  idempotent migration coverage.
- Contract tests for CLI commands, OpenAPI paths, connector manifests, MCP
  schemas, skills, and versioned formats.
- Relay: per-device token expiry, ETag metadata, `If-Match` optimistic
  concurrency, and `sync push --if-match`.
- Load smoke (`make load`) with concurrent writers, Dockerfile, root CI, and a
  coverage target.
- 191 unit tests.

## 0.8.0 — production hardening sprint

- Runtime: atomic run leases + heartbeat, lease release, step timeouts on the
  main thread, and cancellation checks between steps.
- Retention: `maintenance.prune` removes old operational rows, keeps pending
  runs, purges expired model cache, and runs in the scheduler maintenance job.
- Backups: HMAC-signed with the device master key; `restore --trust` for
  intentional cross-device restores.
- Sensitivity: `secret`/`health` facts are hidden from list/search/export/MCP
  and briefs by default; `--include-sensitive` opts in.
- Model cache: encrypted with the master key; plaintext/legacy cache is purged.
- API: Host allowlist, generic 500s, persistent per-token rate limiting, and a
  Prometheus `/metrics` endpoint.
- MCP: executable allowlist and scoped environment for child processes.
- OAuth: per-connector file locks, revoke, and scope-drift detection.
- Connectors: 429/5xx retry with backoff; Google/Microsoft pagination.
- Relay: TLS termination, DELETE, retention, optional per-device tokens, and device admin CLI.
- 175 unit tests.

## 0.7.0 — write actions and Microsoft 365

- Microsoft identity platform OAuth provider.
- Microsoft 365 Calendar/Mail read adapters.
- Connector action framework: Google Calendar create/move, Gmail draft/send,
  Microsoft Calendar create/move, Microsoft Mail draft/send.
- `connector.call` tool/CLI/API with `external` risk and approval gating;
  connector actions are non-idempotent and audited.
- Google scopes upgraded to calendar write and Gmail modify/send.
- 150 unit tests.

## 0.6.0 — MCP server and E2EE relay

- Daythread can run as an MCP stdio server (`dt mcp serve`), exposing briefs,
  memory search, tasks, calendar, approvals, connectors, and approval-gated
  plan proposals. Read-only by default; `--allow-write` opts into memory writes.
- `runtime.propose_plan(force_approval=True)` makes non-read plans require human
  approval before execution.
- Self-hosted relay (`dt relay serve`) stores only opaque encrypted sync
  bundles; `dt sync push|pull|devices` move E2EE bundles between devices.
- Relay writes are `0600`, token-authenticated, size-limited, and validated.
- MCP client cleanup no longer leaks pipe handles.
- 143 unit tests.

## 0.5.0 — real OAuth connectors

- OAuth2 authorization-code + PKCE with a loopback callback server, state
  validation, and configurable provider endpoints.
- Encrypted OAuth token storage via `SecretVault`, with automatic refresh on
  expiry.
- Google Calendar and Gmail read connectors with injectable HTTP transport.
- `dt connector auth <id>`; OAuth-aware sync/disconnect; connector sync tool
  and API pass the local home for token access.
- 135 unit tests.

## 0.4.0 — security, sync, and evaluation

- Schema v4: encrypted `secrets` table and FTS5 `facts_fts` with external-content
  triggers and automatic rebuild.
- Memory search uses FTS5 ranking with a LIKE fallback when FTS is unavailable.
- Optional `cryptography` extra: AES-256-GCM + scrypt, local master key with
  `0600`, encrypted `SecretVault`, and `secret:` references for MCP env.
- E2EE file sync: passphrase-encrypted bundles, verify and merge/replace import;
  secrets remain device-local and are not synced.
- Daythread Bench v0: memory insert/search, proactivity, runtime plans, and
  safety checks in a throwaway home.
- CLI: `secret`, `sync export|import|verify`, `bench`.
- 122 unit tests.

## 0.3.0 — agent infrastructure

- Schema v3: model usage/cache, MCP servers, notifications, scheduler runs,
  and query indexes.
- Model gateway: pluggable providers, `local.echo`, OpenAI-compatible HTTP
  provider, fallback chain, response cache, daily token budget, usage ledger.
- MCP: minimal JSON-RPC 2.0 stdio client, server registry, tool discovery,
  `mcp.call` through the approval gate, and a fixture server test.
- Scheduler: idempotent `morning`/`evening`/`maintenance` ticks, notification
  outbox, quiet-hour deferral, `daemon` loop.
- Backup/restore: checksummed versioned bundles, secret redaction, verify and
  merge/replace restore.
- API hardening: request IDs, per-token rate limiting, pagination,
  OpenAPI document, model/tick/notification endpoints.
- Domain errors with stable HTTP codes.
- CLI: `ask`, `model`, `mcp`, `tick`, `daemon`, `notifications`, `scheduler`,
  `backup`, `restore`, `serve --rotate-token`.
- 108 unit tests.

## 0.2.0 — industrial baseline

- Schema v2 with forward-only migrations.
- Memory: tags, sensitivity, metadata, score, supersede, history, conflicts,
  deterministic extraction, JSON import.
- Runtime: validated DAG plans, policy aggregation, approval gate, idempotency
  keys, retries, checkpoints, resume, cancel, multi-step undo, budgets.
- Tools: read/write/external/destructive tiers, capabilities, deterministic IDs,
  allowlisted sandbox execution, connector sync.
- Proactivity: signals, candidate scoring, interrupt budget, quiet hours,
  delivery deferral, feedback.
- Connectors: manifest registry, mock calendar/mail providers, idempotent sync.
- Skills: declarative validated plans with explicit permissions.
- Local HTTP API with bearer auth on `127.0.0.1`.
- Operations: audit log, metrics, config settings, doctor, JSON/Markdown export.
- 75 unit tests.

## 0.1.0 — first baseline

- Local SQLite memory, policy gate, hash-chained run ledger, approval/undo.
- Morning and evening briefs, local tools, CLI, JSON/Markdown export.
- 25 unit tests.
