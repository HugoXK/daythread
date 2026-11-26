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
  comparison, and serves no CORS headers.
- No credentials or telemetry are collected.

## Out of scope

- Vulnerabilities requiring a compromised local account or physical access.
- Prompt-injection, model, and real OAuth connector issues (not implemented in
  v0.2; mock connectors only).
- Denial of service against a local single-user process.

## Hardening notes

- Treat `DAYTHREAD_HOME` as sensitive; it contains personal facts, notes, drafts,
  the SQLite database, and the API token.
- Review `dt ledger <run-id>` before approving external actions.
- Keep the default `ask` policy for `external` and `destructive`.
- Do not expose `dt serve` beyond loopback.
- Rotate `api_token` by deleting it and restarting the server.
