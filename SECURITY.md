# Security Policy

## Scope

This repository is a local-first reference implementation. It performs no
network calls and stores data under `$DAYTHREAD_HOME` (default `~/.daythread`).

The optional local API binds `127.0.0.1` only and requires a bearer token stored
in `$DAYTHREAD_HOME/api_token` with `0600` permissions.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Contact the
maintainer privately:

- Email: hugo2001xk@gmail.com
- Or use GitHub's private vulnerability reporting for this repository.

Include:

1. A description of the issue and affected version.
2. Reproduction steps (minimal proof of concept if possible).
3. Impact assessment (data exposure, unauthorized action, etc.).
4. Any suggested fix.

You will receive an acknowledgement within 72 hours. Please allow time for a
fix before public disclosure.

## Security invariants

The baseline is designed to preserve these invariants:

- No external or destructive action executes without an explicit `approve`.
- Every run step is hash-chained; tampering is detectable.
- External/destructive steps that may have partially executed are not silently
  retried on resume; they require review.
- Undo either restores the prior state or the action is marked irreversible.
- Tools never read or write outside `DAYTHREAD_HOME`.
- Shell execution is allowlisted, path-restricted, timeout-bounded, and gated by
  the `destructive` policy tier.
- The local API rejects unauthenticated requests, uses constant-time token
  comparison, enforces per-token rate limits, and serves no CORS headers.
- MCP servers are explicitly registered, executed without a shell, and tool
  calls default to the `external` risk tier.
- Backups are checksummed and redact known secrets before writing.
- Secrets are encrypted with AES-256-GCM; the master key is `0600` and stays on
  the device. E2EE sync bundles are authenticated with AES-GCM and exclude
  device-local secrets.
- OAuth uses authorization-code + PKCE with `state` validation, a loopback-only
  callback, least-privilege read/write scopes, and AES-GCM encrypted tokens for
  Google, Microsoft 365, Feishu, and Lark. Client secrets are referenced via
  `env:` or `secret:`, not stored in plaintext.
- The MCP server is read-only by default; write tools require `--allow-write`,
  and plan proposals from external agents require human approval.
- Connector write actions are `external` by default, run only after approval,
  are audited, and are treated as non-idempotent. Sent mail cannot be undone.
- Google now requests calendar write and Gmail modify/send scopes; Microsoft 365
  requests `Calendars.ReadWrite`, `Mail.ReadWrite`, and `Mail.Send`. Users can
  revoke access at the provider at any time.
- The sync relay stores only opaque AES-GCM bundles, requires a bearer token,
  writes `0600` files (including `devices.json`), enforces a body-size limit,
  supports TLS, DELETE, retention, and optional per-device tokens. Rotate tokens and use TLS remotely.
- Runs use an atomic lease and heartbeat; only one executor can claim a run.
  External/MCP/connector/shell steps run in an isolated subprocess and are
  killed by process group on timeout. Local steps use a main-thread signal
  timeout; cancellation is checked between steps.
- Backups are HMAC-signed with the device master key. Restoring a signed backup
  on a new home requires the key or an explicit `--trust`.
- `secret`/`health` facts are hidden from list/search/export/MCP/briefs unless
  explicitly included. Model cache entries are encrypted with the master key.
- The local API validates Host, uses persistent per-token rate limits, and does
  not return internal error details.
- MCP child processes use an executable allowlist and a scoped environment.
- OAuth refreshes are serialized per connector; tokens can be revoked; scope
  drift requires re-authorization.
- Privacy: subject export includes sensitive facts/history; soft forget
  tombstones; hard forget removes facts, history, and matching events; tombstone
  purge is explicit.
- Model egress: `model.egress=local_only` blocks remote providers; retries are
  bounded; streaming does not bypass the daily token budget.
- Webhooks: HMAC-SHA256 over `timestamp.body`, timestamp replay window,
  per-source secrets in the encrypted vault, rate limits, and dedupe.
- Sync cursors (`syncToken`, `deltaLink`) are stored per connector and may contain
  opaque provider identifiers; they are not treated as secrets but are local-only.
- `dt ui` serves on loopback and embeds the bearer token in the page; disable it
  with `api.ui_enabled=0` if that model is not acceptable.
- Release manifests can be HMAC-signed with `DAYTHREAD_RELEASE_KEY`; unsigned
  releases are checksummed but not authenticated.
- The macOS SwiftUI shell runs the local `dt` binary with the user's
  permissions; it does not add its own sandbox.
- MCP resources never expose `secret`/`health` facts.
- Relay device tokens can expire; `ETag`/`If-Match` prevents silent overwrites
  when clients opt in.
- No credentials or telemetry are collected.

## Out of scope

- Vulnerabilities requiring a compromised local account or physical access.
- Prompt-injection, model-provider, and third-party OAuth provider issues on
  the provider side. Local OAuth flow and token storage are in scope.
- Denial of service against a local single-user process.
- Loss of the `master.key` file (encrypted secrets cannot be recovered).
- An insecurely exposed relay or shared relay token. The relay is a self-hosted
  component; operators are responsible for TLS, network ACLs, and retention.
- Backups restored with `--trust` bypass signature verification by design; only
  use it for a backup you have transported securely.
- A malicious MCP server that is allowlisted and explicitly registered. The
  allowlist and subprocess isolation are guardrails; macOS `sandbox-exec` is an
  optional OS-level sandbox, not a cross-platform container.
- Partial side effects when a non-idempotent connector action times out. Such
  runs require review before resume.

## Hardening notes

- Treat `DAYTHREAD_HOME` as sensitive; it contains personal facts, notes, drafts,
  the SQLite database, and the API token.
- Review `dt ledger <run-id>` before approving external actions.
- Keep the default `ask` policy for `external` and `destructive`.
- Do not expose `dt serve` beyond loopback.
- Rotate `api_token` by deleting it and restarting the server.
