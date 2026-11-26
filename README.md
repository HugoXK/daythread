# Daythread · 朝夕

> **Your day, dawn to dusk — local-first, auditable, yours.**

Daythread is a **local-first personal agent reference implementation**: living
memory, multi-step plans, a policy/approval gate, a tamper-evident ledger,
proactivity budgets, mock connectors, declarative skills, and a local HTTP API.
Everything runs on your machine with **zero third-party dependencies**.

This is the working baseline for the design in
[`../docs/daythread-design.md`](../docs/daythread-design.md). It is intentionally
written in Python 3.9+ standard library so the core mechanics can be run,
audited, and ported to the Rust/Tauri target without a build toolchain.

## Status

**v0.2.0 — industrial baseline.** Interfaces may still change. The goal is a
small, correct, auditable core with tests (75 at this milestone), not feature
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
- **Connectors**: manifest-based registry, mock calendar/mail providers,
  connect/disconnect, idempotent sync, rate limits, and local item storage.
- **Skills**: declarative plans with explicit capabilities, builtin skills,
  install/uninstall, and validation.
- **Local HTTP API**: bearer-token auth, health, brief, facts, runs,
  approvals, candidates, connectors, and metrics. Binds `127.0.0.1` only.
- **Operations**: schema migrations, `doctor`, structured audit log, metrics,
  config settings, JSON/Markdown export.

No network calls, no model calls, no telemetry. Real OAuth connectors and an
LLM extraction worker are the next milestone.

## Quickstart

```bash
cd daythread
python3 -m daythread demo
python3 -m daythread brief --evening
python3 -m daythread plan "send maya@example.com | Deck | Latest" --execute
python3 -m daythread proactive
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

dt signal add|list
dt proactive [--deliver]
dt candidate list
dt feedback <candidate-id> accept|ignore|mute

dt connector list|connect|disconnect|sync|items
dt skill list|show|install|run|uninstall

dt config show|set
dt metrics | audit | export [--format md|json]
dt serve [--host 127.0.0.1] [--port 8765] [--print-token]
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

# Proactivity with a hard daily budget (default 3).
dt signal add --source demo --kind note --payload '{"title":"Review launch"}'
dt proactive --deliver

# Mock connectors and skills.
dt connector connect mock.gmail
dt connector sync mock.gmail
dt skill run inbox.triage

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
       └─ skills.py      declarative, validated plans
  └─ brief.py            morning / evening briefs
  └─ export.py           JSON / Markdown export
  └─ obs.py              audit log, metrics, structured logging
  └─ server.py           local-only bearer-token HTTP API
  └─ migrations.py       forward-only schema migrations (v2)
```

Data lives in `$DAYTHREAD_HOME/daythread.db` (default `~/.daythread`).
Notes, drafts, sandbox files, and the API token live under the same home.
Nothing leaves the machine.

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
- **Local API**: `127.0.0.1` only, bearer token in a `0600` file, no CORS.
- **No secrets**: no credentials or telemetry in this baseline.

See [`SECURITY.md`](SECURITY.md) for reporting and scope.

## Tests

```bash
make test
# or
python3 -m unittest discover -s tests -v
```

Coverage includes migrations, memory history/supersede/conflicts/extraction,
planner validation/cycles, multi-step runtime, retries, resume, budgets,
proactivity budgets/feedback, connectors, skills, the HTTP API, and CLI.

## Roadmap

1. LLM-assisted extraction and planning (provider-agnostic, opt-in).
2. Real MCP/OAuth connectors with a token vault and contract tests.
3. Rust daemon + Tauri desktop shell, ported from this reference.
4. Daythread Bench harness (memory, proactivity, execution, safety).
5. End-to-end encryption for optional multi-device sync.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
