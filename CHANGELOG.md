# Changelog

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
