# Changelog

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
