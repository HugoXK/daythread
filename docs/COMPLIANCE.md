# Compliance and operations guide

This document maps Daythread's local-first controls to common privacy and
security expectations. It is a **design and operations guide, not a
certification**. Daythread is not SOC 2 certified, not HIPAA covered, and has no
hosted multi-tenant isolation.

## 1. Data inventory and classification

| Data | Location | Sensitivity | Controls |
|---|---|---|---|
| Facts, history, tasks, calendar, messages | `daythread.db` | personal / health / secret | local-only by default, sensitivity filters, DSAR |
| OAuth tokens, webhook secrets, model API refs | encrypted `secrets` table | secret | AES-256-GCM, `master.key` 0600 |
| Backups | `backups/*.json` | personal | HMAC or Ed25519 signature, optional AES-GCM passphrase |
| Sync bundles | `sync/`, relay | personal | AES-GCM, relay sees ciphertext only |
| Model cache | `daythread.db` | personal | AES-GCM with master key; plaintext legacy purged |
| Audit, metrics, run ledger | `daythread.db` | operational | retention, tamper-evident run hash chain |
| Connector cursors | `connector_state` | opaque provider IDs | local-only, clearable per connector |

## 2. Lawful basis and consent

- Local use is under the operator's control; no telemetry is sent.
- OAuth connectors require explicit per-connector authorization with
  least-privilege scopes; revoke with `dt connector revoke <id>`.
- Model egress can be disabled with `dt config set model.egress local_only`.
- Webhooks require per-source HMAC secrets and a timestamp replay window.

## 3. Data subject rights (DSAR)

```bash
dt privacy export --subject maya --out maya.json   # includes sensitive + history
dt privacy forget --subject maya                   # tombstone
dt privacy forget --subject maya --hard            # remove facts/history/events
dt privacy purge --older-than 30                   # purge old tombstones
```

Limitations: hard forget removes local facts/history and events containing the
subject id. It does not reach provider-side data at Google/Microsoft/Feishu/Lark,
model providers, or previously exported backups. Those must be handled at the
provider/backup level.

## 4. Retention

```bash
dt maintenance stats
dt maintenance prune --older-than 90 --keep-runs 200 --vacuum
```

The scheduler maintenance job also prunes according to
`maintenance.retention_days` and `maintenance.keep_runs`. Pending runs are never
pruned. Facts are user data and are not pruned automatically.

## 5. Encryption and keys

- Secrets: AES-256-GCM with a per-device `master.key` (0600).
- Sync/encrypted backups: scrypt-derived AES-GCM passphrase keys.
- Transfers: `dt serve --certfile --keyfile` and relay TLS; otherwise loopback
  only.
- Backups: HMAC-SHA256 or Ed25519 signature; public key embedded for Ed25519.
- Key loss means encrypted secrets/backups cannot be recovered. Back up
  `master.key` separately, or use passphrase-encrypted backups.

## 6. Access control

- Local API: bearer token in `api_token` (0600), Host allowlist, persistent
  per-token rate limit, loopback default.
- Relay: admin token plus optional expiring per-device tokens; bundles are
  opaque ciphertext.
- MCP server: read-only by default; write tools require `--allow-write`; plan
  proposals require approval and default to `taint=untrusted`.
- Connector write actions: `external` risk, approval required, non-idempotent.

## 7. Logging and monitoring

- Audit log: `dt audit`, local SQLite.
- Metrics: `dt metrics` and Prometheus `/metrics`; Grafana starter dashboard in
  `deploy/grafana/`.
- Run ledger: `dt ledger <run> --verify` detects tampering.
- Structured logging is opt-in via `DAYTHREAD_LOG_LEVEL`.

## 8. SOC 2-style control mapping

| Control area | Daythread mechanism |
|---|---|
| Logical access (CC6) | loopback API, bearer token, Host allowlist, OAuth scopes, relay device tokens |
| Change management (CC8) | migrations, CI matrix, contract tests, release manifest/SBOM |
| Monitoring (CC7) | audit log, metrics, run ledger, Prometheus/Grafana |
| Risk mitigation (CC9) | approval gate, taint, sandbox (sandbox-exec/bwrap/Docker), subprocess timeouts |
| Data handling | sensitivity filters, DSAR, retention, encryption, encrypted backups |
| Availability (A1) | atomic leases, resume/cancel, backup/restore, relay ETag conflicts |

## 9. Incident response

1. **Contain**: stop `dt serve`/relay; rotate `api_token` and relay tokens
   (`dt serve --rotate-token`, update `devices.json`).
2. **Revoke**: `dt connector revoke <id>`; rotate provider OAuth apps if needed.
3. **Assess**: `dt audit`, `dt ledger`, `dt maintenance stats`, relay bundle
   metadata.
4. **Eradicate**: delete compromised connectors/secrets; restore from a signed
   backup on a clean host.
5. **Notify**: follow applicable law/contracts; provide DSAR export on request.
6. **Learn**: add a regression test or contract test; update this guide.

## 10. Subprocessors and third parties

| Provider | Purpose | Default |
|---|---|---|
| Local model (`local.echo`) | deterministic local model | no network |
| OpenAI-compatible endpoint | optional remote model | opt-in |
| Google Calendar/Gmail | optional connector | OAuth opt-in |
| Microsoft 365 | optional connector | OAuth opt-in |
| Feishu/Lark | optional connector | OAuth opt-in |
| MCP servers | optional tools | explicit registration + allowlist |
| E2EE relay | optional sync | self-hosted |

## 11. Known gaps

- No SOC 2/ISO certificate; no formal audit.
- No HIPAA/BAA; health features are lifestyle support only.
- No hosted multi-tenant isolation; single-user local model.
- Provider-side deletion and model-provider retention are out of scope.
- Relay is self-hosted; operators own TLS, ACLs, and retention.
