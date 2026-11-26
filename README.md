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

**v0.9.0 — DSAR, test matrix, and packaging.** Interfaces may still change. The
goal is a small, correct, auditable core with tests (191 at this milestone), not
feature breadth.

## What works today

- **Living memory**: structured facts with provenance, confidence, tags,
  sensitivity, score, edit history, supersede, conflict detection, tombstones,
  deterministic text extraction, JSON import/export, and FTS5-ranked search
  with a LIKE fallback.
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
- **MCP client**: JSON-RPC 2.0 stdio client, server registry, tool discovery,
  and `mcp.call` behind the approval gate.
- **MCP server**: `dt mcp serve` exposes briefs, memory, tasks, calendar,
  approvals, connectors, and approval-gated plan proposals to other agents.
  Read-only by default.
- **Connectors**: manifest-based registry, mock providers, **OAuth2 + PKCE**
  with a loopback callback, encrypted token storage and refresh, Google and
  Microsoft 365 Calendar/Mail adapters, read sync with retry/backoff and
  pagination, and approval-gated write actions (create/move events,
  draft/send mail).
- **Skills**: declarative plans with explicit capabilities, builtin skills,
  install/uninstall, and validation.
- **Backup/restore**: checksummed versioned bundles, secret redaction, verify,
  merge or replace restore.
- **Secrets**: optional AES-256-GCM + scrypt vault with a `0600` master key,
  and `secret:` references for MCP environment variables.
- **E2EE sync**: passphrase-encrypted bundles, verify/import, plus a
  self-hosted relay (`dt relay serve`) for multi-device push/pull; secrets stay
  device-local and the relay only sees ciphertext.
- **Daythread Bench v0**: memory insert/search, proactivity, runtime plans, and
  safety checks in a throwaway home.
- **Local HTTP API**: bearer auth, Host allowlist, persistent per-token rate
  limits, request IDs, pagination, OpenAPI document, facts, runs, approvals,
  candidates, notifications, connectors, models, ask, and Prometheus metrics.
  Binds `127.0.0.1` only.
- **Hardening**: atomic run leases, step timeouts, retention/pruning, signed
  backups, encrypted model cache, sensitivity filters, MCP command allowlist,
  OAuth revoke/scope checks, relay TLS/retention/device tokens.
- **Operations**: schema migrations, `doctor`, structured audit log, metrics,
  config settings, JSON/Markdown export, migration matrix, contract tests, load
  smoke, Dockerfile, and CI.
- **Privacy/DSAR**: per-subject export, soft/hard forget, tombstone purge, and
  audit records.

No telemetry. Network access only happens if you explicitly configure an
OpenAI-compatible provider or an MCP server.

## Quickstart

```bash
cd daythread
python3 -m daythread demo
python3 -m daythread ask "what should I focus on?"
python3 -m daythread tick --job morning
python3 -m daythread notifications
python3 -m daythread bench
make test
```

Install the `dt` command:

```bash
python3 -m pip install -e .
# optional encryption/sync support:
python3 -m pip install -e ".[crypto]"
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

dt mcp add|list|remove|tools|call|serve
dt signal add|list
dt proactive [--deliver]
dt candidate list
dt feedback <candidate-id> accept|ignore|mute
dt tick [--job morning|evening|maintenance]
dt daemon [--interval 60] [--max-ticks N]
dt notifications | scheduler list

dt connector list|auth|revoke|connect|disconnect|sync|call|items
dt skill list|show|install|run|uninstall

dt secret set <name> | list | rm
dt sync export|import|verify|push|pull|devices [--passphrase-env DAYTHREAD_PASSPHRASE]
dt sync push ... [--if-match ETAG]
dt relay serve [--dir DIR] [--port 8788] [--token-env DAYTHREAD_RELAY_TOKEN]
dt relay devices add|list|remove [--file PATH]
dt bench [--facts N] [--runs N]

dt config show|set
dt metrics | audit | export [--format md|json] [--include-sensitive]
dt maintenance stats|prune [--older-than N] [--keep-runs N] [--vacuum]
dt privacy export|forget|purge
dt fact list|search ... [--include-sensitive]
dt backup [--out DIR] | restore <file> [--verify] [--replace] [--trust]
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

# MCP client: connect to another MCP server.
dt mcp add echo --command "python3 /path/to/server.py"
dt mcp tools echo
dt mcp call echo echo --arg text=hello
dt approve <run-id>

# MCP server: expose Daythread to Claude/Cursor (read-only by default).
dt mcp serve
dt mcp serve --allow-write

# Scheduler and notifications.
dt tick --job morning
dt notifications
dt daemon --interval 60

# Mock connectors and skills.
dt connector connect mock.gmail
dt connector sync mock.gmail
dt skill run inbox.triage

# Real Google connectors (requires the crypto extra).
python3 -m pip install -e ".[crypto]"
dt config set oauth.google.client_id "<client-id>"
dt config set oauth.google.client_secret_ref "env:GOOGLE_CLIENT_SECRET"
dt connector auth google.calendar --no-browser   # open the printed URL
dt connector auth google.gmail --no-browser
dt connector sync google.calendar
dt connector sync google.gmail

# Write actions (always approved before execution).
dt connector call google.calendar create_event \
  --arg title="Design review" --arg start=2026-11-26T15:00:00Z --arg end=2026-11-26T16:00:00Z
dt connector call google.gmail create_draft \
  --arg to=maya@example.com --arg subject=Deck --arg body="Latest version."
dt approve <run-id>

# Microsoft 365 OAuth.
dt config set oauth.microsoft.client_id "<client-id>"
dt config set oauth.microsoft.client_secret_ref "env:MS_CLIENT_SECRET"
dt connector auth microsoft.calendar --no-browser
dt connector auth microsoft.mail --no-browser
dt connector sync microsoft.calendar
dt connector call microsoft.mail create_draft \
  --arg to=maya@example.com --arg subject=Deck --arg body="Latest version."

# Backup and restore (signed with the device master key).
dt backup
dt restore ~/.daythread/backups/daythread-*.json --verify
dt restore ~/.daythread/backups/daythread-*.json
dt restore ~/.daythread/backups/daythread-*.json --trust   # new device / new home

# Retention and housekeeping.
dt maintenance stats
dt maintenance prune --older-than 90 --keep-runs 200 --vacuum

# Privacy / DSAR.
dt privacy export --subject maya --out maya.json
dt privacy forget --subject maya
dt privacy forget --subject maya --hard
dt privacy purge --older-than 30

# Load smoke and container build.
make load
make docker

# Encrypted secrets and E2EE file sync.
dt secret set OPENAI_KEY            # prompts on a TTY, or pipe stdin
dt sync export
DAYTHREAD_PASSPHRASE=... dt sync import ~/.daythread/sync/daythread-sync-*.json --verify

# E2EE multi-device relay.
dt relay serve --port 8788
DAYTHREAD_RELAY_TOKEN=... dt sync push --relay http://127.0.0.1:8788 --device laptop
DAYTHREAD_RELAY_TOKEN=... dt sync pull --relay http://127.0.0.1:8788 --device laptop
DAYTHREAD_RELAY_TOKEN=... dt sync devices --relay http://127.0.0.1:8788

# Benchmark the local core.
dt bench --facts 500 --runs 50

# Local API.
dt serve --print-token
dt serve --port 8765
```

Register this loopback redirect URI in the Google Cloud console for the client:
`http://127.0.0.1` (dynamic port). Tokens are AES-GCM encrypted at rest; sync
happens only after an explicit authorization.

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
       ├─ connectors.py  manifests, mock + Google providers, idempotent sync
       ├─ oauth.py       OAuth2 + PKCE, loopback callback, encrypted tokens
       ├─ mcp.py         JSON-RPC 2.0 stdio client + server registry
       ├─ mcp_server.py  expose Daythread as an MCP stdio server
       ├─ models.py      provider routing, cache, budgets, usage
       ├─ scheduler.py   anchored ticks, notifications, maintenance
       ├─ backup.py      checksummed, HMAC-signed backup/restore
       ├─ privacy.py     per-subject export/forget and tombstone purge
       ├─ crypto.py      optional AES-GCM/scrypt + encrypted secret vault
       ├─ sync.py        passphrase-encrypted E2EE file sync + relay transport
       ├─ relay.py       self-hosted opaque bundle relay
       ├─ bench.py       Daythread Bench v0
       └─ skills.py      declarative, validated plans
  └─ brief.py            morning / evening briefs
  └─ export.py           JSON / Markdown export
  └─ obs.py              audit log, metrics, structured logging
  └─ server.py           local-only API: auth, rate limits, pagination
  └─ migrations.py       forward-only schema migrations (v4)
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
- **Backups**: checksummed, HMAC-signed, versioned, and redact known secrets.
  `restore --trust` is required when the master key is not present.
- **Runtime leases**: only one executor can claim a run; leases expire and are
  released on completion/failure. Steps have timeouts on the main thread and
  cancellation is checked between steps.
- **Retention**: old events, steps, audit, usage, messages, and connector items
  can be pruned; pending runs are always kept.
- **Sensitivity**: `secret`/`health` facts are hidden from list/search/export/
  MCP/briefs unless `--include-sensitive` is passed.
- **API**: Host allowlist, persistent rate limits, no internal error details.
- **MCP**: executable allowlist plus a scoped child environment.
- **Secrets**: encrypted with AES-GCM; the master key is `0600` and never
  leaves the device. `secret:` references keep literals out of MCP configs.
- **E2EE sync**: bundles are authenticated with AES-GCM; secrets are excluded
  from sync by design.
- **OAuth**: authorization-code + PKCE, `state` validation, loopback-only
  callback, encrypted tokens, and refresh-on-expiry. Client secrets come from
  `env:` or `secret:` references, never the command line.
- **MCP server**: read-only by default; write tools are hidden unless
  `--allow-write` is set; plans proposed by external agents require approval.
- **Connector actions** are `external` by default: a run is created and must be
  approved before any event is created, moved, drafted, or sent.
- **Relay**: stores only AES-GCM ciphertext, requires a bearer token, writes
  `0600` files, enforces a body-size limit, supports TLS/certificates, DELETE,
  retention, and optional per-device tokens. Put it behind TLS for remote use.

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

1. Feishu/Lark and more OAuth providers.
2. Rust daemon + Tauri desktop shell, ported from this reference.
3. Expand Daythread Bench into a public, reproducible benchmark suite.
4. Hosted deployment, observability backend, and compliance program.
5. Server-side hard step timeouts and an OS-level MCP sandbox.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
