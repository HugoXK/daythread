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

The Markdown form lists the full `hash` and `prev_hash` of every step (the JSON
form is authoritative for tooling) and includes an `## Approvals` section with
the real approver, decision and step time for runs that needed approval.

Undo recipes keep only non-content fields (`kind`, `path`, `table`, `id`, `ids`,
`target`): `previous`/`fields` never leave the ledger, so prior file or row
content is not exported. Remaining values are redacted (`obs.redact()` sensitive
keys) and ids of `secret` / `health` facts are dropped, so a receipt does not
carry those references.

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
