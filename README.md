# Daythread · 朝夕

> **Your day, dawn to dusk — local-first, auditable, yours.**

Daythread is a **local-first personal agent reference implementation**: living
memory, multi-step plans, a policy/approval gate, a tamper-evident ledger,
proactivity budgets, connectors, declarative skills, a model gateway, an MCP
client, a scheduler, backups, and a local HTTP API. Everything runs on your
machine with **zero third-party dependencies**.

This is the working baseline for the design in
[`../docs/daythread-design.md`](../docs/daythread-design.md). It is intentionally
written in Python 3.9+ standard library so the core mechanics can be run,
audited, and ported to the Rust/Tauri target without a build toolchain.

## Status

**v0.3.0 — agent infrastructure.** Interfaces may still change. The goal is a
small, correct, auditable core with tests (108 at this milestone), not feature
breadth.

## What works today

- **Living memory**: structured facts with provenance, confidence, tags,
  sensitivity, score, edit history, supersede, conflict detection, tombstones,
  deterministic text extraction, and JSON import/export.
- **Plan runtime**: validated DAGs, policy aggregation, approval gate,
  idempotency keys, per-step retries, checkpoints, resume, cancel, multi-step
  undo, and execution budgets.
- **Tamper-evident ledger**: append-only events and hash-chained run steps;
  `dt ledger <run> --verify` recomputes the chain.
- **Proactivity**: signals, candidate scoring, interrupt budget, quiet hours,
  delivery deferral, and accept/ignore/mute feedback.
- **Scheduler**: idempotent morning/evening/maintenance ticks, notification
  outbox, quiet-hour queueing, and a daemon loop.
- **Model gateway**: pluggable providers, local deterministic provider,
  OpenAI-compatible HTTP provider, fallback chain, response cache, daily token
  budget, and usage ledger.
- **MCP**: JSON-RPC 2.0 stdio client, server registry, tool discovery, and
  `mcp.call` behind the approval gate.
- **Connectors**: manifest-based registry, mock calendar/mail providers,
  connect/disconnect, idempotent sync, and local item storage.
- **Skills**: declarative plans with explicit capabilities, builtin skills,
  install/uninstall, and validation.
- **Backup/restore**: checksummed versioned bundles, secret redaction, verify,
  merge or replace restore.
- **Local HTTP API**: bearer auth, rate limits, request IDs, pagination,
  OpenAPI document, facts, runs, approvals, candidates, notifications,
  connectors, models, ask, and metrics. Binds `127.0.0.1` only.
- **Operations**: schema migrations, `doctor`, structured audit log, metrics,
  config settings, JSON/Markdown export.

No telemetry. Network access only happens if you explicitly configure an
OpenAI-compatible provider or an MCP server.

## Quickstart

```bash
cd daythread
python3 -m daythread demo
python3 -m daythread ask "what should I focus on?"
python3 -m daythread tick --job morning
python3 -m daythread notifications
make test
```

Install the `dt` command:

```bash
python3 -m pip install -e .
dt doctor
```

Use a custom home:

```bash
export DAYTHREAD_HOME="$PWD/.daythread"
dt init
```

## Command map

```text
dt init | migrate | doctor | demo

dt fact add|list|show|edit|rm|search|extract|history|conflicts|import
dt task list|create|done|update
dt brief [--evening]
dt plan "<intent>" [--execute] [--file plan.json] [--idempotency-key K]
dt act <tool> --arg k=v [--idempotency-key K]
dt runs | show | approve | reject | undo | resume | cancel | ledger
dt policy show|set

dt ask "<text>" [--task NAME] [--no-cache]
dt model list|usage

dt mcp add|list|remove|tools|call
dt signal add|list
dt proactive [--deliver]
dt candidate list
dt feedback <candidate-id> accept|ignore|mute
dt tick [--job morning|evening|maintenance]
dt daemon [--interval 60] [--max-ticks N]
dt notifications | scheduler list

dt connector list|connect|disconnect|sync|items
dt skill list|show|install|run|uninstall

dt config show|set
dt metrics | audit | export [--format md|json]
dt backup [--out DIR] | restore <file> [--verify] [--replace]
dt serve [--host 127.0.0.1] [--port 8765] [--print-token] [--rotate-token]
```

### Examples

```bash
# Multi-step plan: draft, then external send. Approval is required.
dt plan "send maya@example.com | Deck | Latest" --execute
dt approve <run-id>

# Inspect and verify the audit chain.
dt ledger <run-id> --verify

# Reversible multi-step plan (custom JSON).
cat > plan.json <<'JSON'
{"steps": [
  {"id": "s1", "tool": "note.write", "args": {"path": "a.md", "content": "A"}},
  {"id": "s2", "tool": "note.write", "args": {"path": "b.md", "content": "B"}, "depends_on": ["s1"]}
]}
JSON
dt plan --file plan.json --execute
dt undo <run-id>

# Model gateway (local by default; configure a remote provider explicitly).
dt ask "summarize my day"
dt config set model.provider openai.compatible
dt config set model.model gpt-4o-mini
export OPENAI_API_KEY=...
dt ask "summarize my day"

# MCP server and tool call.
dt mcp add echo --command "python3 /path/to/server.py"
dt mcp tools echo
dt mcp call echo echo --arg text=hello
dt approve <run-id>

# Scheduler and notifications.
dt tick --job morning
dt notifications
dt daemon --interval 60

# Mock connectors and skills.
dt connector connect mock.gmail
dt connector sync mock.gmail
dt skill run inbox.triage

# Backup and restore.
dt backup
dt restore ~/.daythread/backups/daythread-*.json --verify
dt restore ~/.daythread/backups/daythread-*.json

# Local API.
dt serve --print-token
dt serve --port 8765
```

## Architecture

```text
CLI (dt) / HTTP API
  └─ runtime.py          plan engine: DAG, policy, approval, retries,
                         idempotency, checkpoints, resume, undo, budgets
       ├─ planner.py     normalize / validate / toposort / preview / intents
       ├─ policy.py      read / write / external / destructive → allow / ask / deny
       ├─ tools.py       capabilities, risk tiers, undo recipes, sandbox exec
       ├─ memory.py      facts, provenance, history, supersede, conflicts, extract
       ├─ ledger.py      append-only events + hash-chained steps
       ├─ proactivity.py signals, candidates, interrupt budget, feedback
       ├─ connectors.py  manifests, mock providers, idempotent sync
       ├─ mcp.py         JSON-RPC 2.0 stdio client + server registry
       ├─ models.py      provider routing, cache, budgets, usage
       ├─ scheduler.py   anchored ticks, notifications, maintenance
       ├─ backup.py      checksummed backup/restore
       └─ skills.py      declarative, validated plans
  └─ brief.py            morning / evening briefs
  └─ export.py           JSON / Markdown export
  └─ obs.py              audit log, metrics, structured logging
  └─ server.py           local-only API: auth, rate limits, pagination
  └─ migrations.py       forward-only schema migrations (v3)
  └─ errors.py           domain errors with stable HTTP codes
```

Data lives in `$DAYTHREAD_HOME/daythread.db` (default `~/.daythread`).
Notes, drafts, sandbox files, backups, the API token, and the notification
outbox live under the same home. Nothing leaves the machine unless you
configure a remote model provider or MCP server.

## HTTP API

Start the server and read the token:

```bash
dt serve --print-token
dt serve --port 8765
```

Then:

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/v1/today
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8765/v1/facts?limit=20&offset=0"
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"hello"}' http://127.0.0.1:8765/v1/ask
curl http://127.0.0.1:8765/v1/openapi.json
```

The API includes request IDs (`X-Request-Id`), per-token rate limiting
(`api.rate_limit_per_minute`, default 120), pagination, and stable error codes.

## Safety model

- **Confirm before external**: `external` and `destructive` default to `ask`.
  Approving a plan is explicit and recorded.
- **Tamper-evident**: every run step is SHA-256 chained over its canonical
  payload plus the previous hash.
- **Idempotency**: external/destructive steps refuse to resume after an
  uncertain outcome; local writes use deterministic IDs or overwrite semantics.
- **Undo or mark irreversible**: tools return an undo recipe or explicitly
  declare `irreversible`; multi-step undo runs in reverse and refuses if any
  step is irreversible.
- **Least authority**: tools stay under `DAYTHREAD_HOME`; shell execution is
  allowlisted, path-restricted, timeout-bounded, and approval-gated.
- **MCP**: servers are explicitly registered, run without a shell, and tool
  calls are `external` by default.
- **Local API**: `127.0.0.1` only, bearer token in a `0600` file, rate limited,
  no CORS.
- **Backups**: checksummed, versioned, and redact known secrets.

See [`SECURITY.md`](SECURITY.md) for reporting and scope.

## Tests

```bash
make test
# or
python3 -m unittest discover -s tests -v
```

Coverage includes migrations, memory history/supersede/conflicts/extraction,
planner validation/cycles, multi-step runtime, retries, resume, budgets,
proactivity budgets/feedback, connectors, skills, model gateway (providers,
cache, budgets, fallback), MCP stdio transport, scheduler ticks, backup/restore,
the HTTP API (auth, pagination, rate limits, request IDs, OpenAPI), and CLI.

## Roadmap

1. Real OAuth connectors with a token vault and contract tests.
2. End-to-end encrypted multi-device sync.
3. Rust daemon + Tauri desktop shell, ported from this reference.
4. Daythread Bench harness (memory, proactivity, execution, safety).
5. Hosted deployment, observability backend, and compliance program.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
