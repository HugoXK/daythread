# CLI reference

`dt` is the only entry point. Data lives in `$DAYTHREAD_HOME` (default
`~/.daythread`). Global flags: `--home`, `--db`, `--json`, `--version`.

## Core

| Command | Purpose |
|---|---|
| `dt init` / `migrate` / `doctor` | initialize, migrate, inspect local state |
| `dt demo` | seed a demo dataset |
| `dt brief [--evening]` | morning / evening brief |
| `dt ask <text> [--stream]` | model gateway (local echo by default) |
| `dt model list|usage` | model config and usage |
| `dt bench [--facts N] [--runs N]` | Daythread Bench v0 |

## Memory and privacy

| Command | Purpose |
|---|---|
| `dt fact add|list|show|edit|rm|search` | structured facts |
| `dt fact extract <text> [--apply]` | deterministic extraction |
| `dt fact history <id>` / `conflicts` / `import <file>` | audit and import |
| `dt privacy export --subject <id> [--out file]` | DSAR export |
| `dt privacy forget --subject <id> [--hard]` | soft/hard forget |
| `dt privacy purge --older-than N` | purge tombstones |

Sensitive (`secret`/`health`) facts are hidden unless `--include-sensitive`.

## Plans, runs, approvals

```bash
dt plan "<intent>" [--execute] [--file plan.json] [--taint untrusted]
dt act <tool> --arg k=v [--taint untrusted]
dt runs | show <id> | ledger <id> --verify
dt receipt <id> [--json] [--out PATH]   # finished runs only; Markdown by default
dt receipt <id> --verify | dt receipt --input FILE --verify  # recompute and check
dt approve <id> | reject <id> | undo <id> | resume <id> | cancel <id>
dt policy show | set <risk> <allow|ask|deny>
```

`taint=untrusted` forces approval for every non-read step.

`dt receipt` projects a *finished* run's ledger into one deterministic document
(`version`, `run`, step `hash`/`payload_digest` rows, `chain_head`, `policy`,
`undo`, and a `digest` over the canonical body). Markdown is the default for
humans; `--json` emits the canonical machine form, and `--out PATH` writes the
same bytes to a file. It is read-only: no table is written, no migration runs,
and writing `--out` never changes run state. Unfinished runs (`pending_approval`,
`running`, ...) and unknown ids exit non-zero instead of producing a partial
receipt. The ledger remains authoritative: `dt ledger <id> --verify`.

`--verify` recomputes the receipt itself, never the database: it checks the
embedded `digest` over the canonical body, every `prev_hash` link, and
`chain_head`. Tampering in an exported file exits non-zero, prints
`verified: false`, and names the first broken step (`first broken step: <seq>`)
when the break is a link; a tampered `payload_digest` has no payload to re-hash,
so it surfaces as a document-level `digest mismatch`. With a run id,
`dt receipt <id> --verify` additionally reconciles against `ledger.verify_run()`,
so a payload or hash edited in the local database is reported at its `seq`.
Missing fields, a bad version, or unreadable JSON exit 1 with `error: …` and no
traceback. `dt ledger <id> --verify` is unchanged.

The Markdown form lists the full `hash` and `prev_hash` of every step (the JSON
form is authoritative for tooling) and includes an `## Approvals` section with
the real approver, decision and step time for runs that needed approval.

Undo recipes keep only non-content fields (`kind`, `path`, `table`, `id`, `ids`,
`target`, and the `connector`/`action` identifiers needed to execute a connector
recipe): `previous`/`fields` never leave the ledger, so prior file or row content
is not exported. Remaining values are redacted (`obs.redact()` sensitive keys)
and ids of `secret` / `health` facts are dropped, so a receipt does not carry
those references. When undo is available the Markdown form ends with the exact
`dt undo <id>` command; a run whose external writes are explicitly irreversible
says so instead of showing an empty recipe list.

`dt undo` verifies the run's hash chain before it applies anything: a tampered
ledger is refused with `ledger failed verification` and no undo step (in
particular no outbound connector call) is attempted. A run containing an
explicitly irreversible step (`message.send`, `mcp.call`, `shell.run`, or a
connector action with no declared recipe) is refused with `contains an
irreversible step`. `shell.run` is a fixed read-only allowlist (`echo`, `pwd`,
`ls`, `cat`, `head`, `tail`, `wc`, `date`, `whoami`) run with `shell=False` and
relative paths only, so that refusal is a conservative registry classification,
not a claim that the command wrote anything.

When a connector action declares an undo recipe, the run's step carries
`kind=connector.delete` plus exactly one target id, and `dt undo` deletes exactly
that one remote object. If the provider accepted the write but returned no event
id, the run fails closed at that step (`returned no id`), the external outcome is
uncertain so `resume` refuses it, and the receipt has no recipe. Clean up
manually: open the connected account's provider console (for example Google
Calendar) with write access, find the event by its title and start time, delete
it, then inspect `dt show <id>` / `dt ledger <id> --verify` and start a new run.

`undo` is one-shot: if any undo step fails the run is left `undo_failed` with
the original payloads and receipt intact, and further `dt undo` calls refuse it
because only completed runs can be undone. Inspect `dt show <id>` /
`dt ledger <id> --verify`, clean up the external object by hand if the provider
call took effect, and start a new run instead of retrying in place.

`resume` refuses a failed run when a non-idempotent step may have partially
executed (its idempotency state is `started` or `failed`). Verify the external
side effect first, inspect `dt show <id>` / `dt ledger <id> --verify`, and start
a new run if a retry is needed; the failed run stays as an audit record.

## Connectors and skills

```bash
dt connector list | auth <id> | revoke <id> | connect <id> | disconnect <id>
dt connector sync <id> [--reset] | state <id> | reset <id>
dt connector call <id> <action> --arg k=v
dt connector items <id>
dt skill list | show <id> | install <file> | run <id> | uninstall <id>
```

`dt connector call` is registered as an external tool whose spec-level fallback
is `irreversible`: only actions that declare a per-action undo recipe (today
`google.calendar create_event`) produce an executable `connector.delete` recipe
and can be undone. Every other write action is marked irreversible in its step
payload with a reason (visible as `irreversible_reason` in `dt show <id> --json`).
A connector handler without an `actions` metadata entry fails closed at
registration and again at call time, before the handler runs. `provides_undo`
is a registry declaration, not a runtime promise: if such a handler ever returns
no recipe, `dt undo` refuses the run before executing any recipe and the receipt
reports undo as unavailable.

## Proactivity, scheduler, webhooks

```bash
dt signal add|list
dt proactive [--deliver] | candidate list | feedback <id> accept|ignore|mute
dt tick [--job morning|evening|maintenance] | daemon [--interval 60]
dt notifications | scheduler list
dt webhook secret set|rm <source> | webhook sign <source> --payload JSON | webhook events
```

## Sync, relay, backup

```bash
dt sync export|import|verify|push|pull|devices [--passphrase-env ENV]
dt sync push --relay URL --device ID [--if-match ETAG] [--on-conflict fail|pull]
dt relay serve [--port 8788] [--certfile cert --keyfile key] [--devices FILE]
dt relay devices add|list|remove [--file PATH]
dt backup [--out DIR] [--passphrase-env DAYTHREAD_BACKUP_PASSPHRASE]
dt restore <file> [--verify] [--replace] [--trust]
```

## MCP and HTTP

```bash
dt mcp add|list|remove|tools|call|serve
dt serve [--host 127.0.0.1] [--port 8765] [--certfile cert --keyfile key]
dt ui [--port 8765] [--no-browser]
```

## Maintenance

```bash
dt maintenance stats
dt maintenance prune --older-than 90 --keep-runs 200 --vacuum
dt audit | metrics | export [--format md|json] [--include-sensitive]
dt config show | set <key> <value>
```

## Environment

| Variable | Purpose |
|---|---|
| `DAYTHREAD_HOME` | data directory |
| `DAYTHREAD_BIN` | desktop shells' `dt` path |
| `DAYTHREAD_PASSPHRASE` | sync passphrase |
| `DAYTHREAD_BACKUP_PASSPHRASE` | encrypted backup passphrase |
| `DAYTHREAD_RELAY_TOKEN` | relay bearer token |
| `DAYTHREAD_RELEASE_KEY` | HMAC release key |
| `DAYTHREAD_LOG_LEVEL` | structured log level |

Exit code `0` on success, `1` on a handled error, non-zero from the OS for
fatal failures.
