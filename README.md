# Daythread · 朝夕

> **Your day, dawn to dusk — local-first, auditable, yours.**

Daythread is a **local-first personal agent baseline**: a structured memory, a
proactivity-ready briefing loop, a policy/approval gate, and a tamper-evident
run ledger — all running on your machine with zero third-party dependencies.

This repository is the **working baseline**, not the final product. It is a
reference implementation of the core mechanics described in
[`../docs/daythread-design.md`](../docs/daythread-design.md), written in Python
3.9+ using only the standard library so it can be run, audited, and ported
without a build toolchain.

## What works today

- **Living memory**: structured facts (`person`/`project`/`preference`/`commitment`),
  with provenance, confidence, edit, delete (tombstone), and search.
- **Append-only events + hash-chained run ledger**: every fact change and agent
  action is recorded; `dt ledger --verify` detects tampering.
- **Policy gate**: risk tiers `read` / `write` / `external` / `destructive`
  mapped to `allow` / `ask` / `deny`.
- **Approval + undo**: external actions block until approved; reversible
  actions can be undone.
- **Briefings**: morning and evening briefs from local calendar, tasks,
  commitments, and pending approvals.
- **Local tools**: write notes, create/complete tasks, add calendar events,
  draft messages, and a simulated external `message.send`.
- **Export**: JSON and Markdown, so memory is never locked in.
- **CLI**: `dt` with a demo dataset.

No network calls, no model calls, no telemetry. Model routing and real
connectors (MCP/OAuth) are the next milestone.

## Quickstart

```bash
cd daythread
python3 -m daythread init
python3 -m daythread demo
python3 -m daythread brief --evening
```

Install as a command:

```bash
python3 -m pip install -e .
dt --help
```

Use a custom home:

```bash
export DAYTHREAD_HOME="$PWD/.daythread"
dt init
```

## A five-minute walkthrough

```bash
# 1. Remember something
dt fact add --type person --subject maya --predicate role --value "co-founder"

# 2. A write action is allowed by default; it is logged and reversible
dt act note.write --intent "capture launch notes" \
  --arg path=launch.md --arg content="3 directions for Tuesday."

# 3. An external action must be approved first
dt act message.send --intent "send the deck" \
  --arg to=maya@example.com --arg subject=Deck --arg body="Latest version attached."

# 4. Inspect and approve
dt runs
dt approve <run-id>

# 5. Audit and undo
dt ledger <run-id> --verify
dt undo <run-id>          # only for reversible actions
```

## Architecture (baseline)

```
CLI (dt)
  └─ runtime.py        run state machine, approval, execution, undo
       ├─ policy.py    risk → allow / ask / deny
       ├─ tools.py     local tools with explicit risk + undo recipes
       ├─ ledger.py    append-only events + hash-chained run steps
       └─ memory.py    facts + provenance + tombstone + search
  └─ brief.py          morning / evening briefs
  └─ export.py         JSON / Markdown export
  └─ db.py             SQLite schema and connection
```

Data lives in `$DAYTHREAD_HOME/daythread.db` (default `~/.daythread`).
Notes and drafts live under the same home. Nothing leaves the machine.

## Safety model

- **Confirm before external**: `external` and `destructive` default to `ask`.
- **Tamper-evident**: every run step is chained with SHA-256 over its canonical
  payload plus the previous hash.
- **Undo or mark irreversible**: tools return an undo recipe or explicitly
  declare `irreversible`.
- **Least authority**: the baseline only touches files under `DAYTHREAD_HOME`.
- **No secrets**: no credentials, tokens, or network access in this baseline.

See [`SECURITY.md`](SECURITY.md) for reporting and scope.

## Tests

```bash
make test
# or
python3 -m unittest discover -s tests -v
```

## Roadmap (next)

1. Memory extraction worker (LLM-assisted, pluggable).
2. MCP connector SDK + OAuth broker (local mock first).
3. Proactivity engine with interrupt budget.
4. Rust daemon + Tauri desktop shell (port from this reference).
5. Daythread Bench harness (memory, proactivity, execution, safety).

## Status

**v0.1.0 — baseline.** Interfaces may change. The goal of this milestone is a
small, correct, auditable core with tests, not feature breadth.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
