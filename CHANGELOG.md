# Changelog

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
