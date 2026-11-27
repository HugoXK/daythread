# Release and supply chain

## Artifacts

```bash
python3 -m pip install -e ".[crypto,dev]"
make release        # wheel + SHA256SUMS + sbom.json + release-manifest.json
make attest         # signed DSSE/in-toto attestation (requires release.pem)
```

Generate an Ed25519 release key once:

```bash
python3 scripts/release.py --generate-key release.pem
python3 scripts/release.py --build --signing-key release.pem
python3 scripts/attest.py --dist dist --signing-key release.pem
```

`release.pem` is git-ignored and must stay offline. The manifest embeds the
public key; the attestation covers every artifact digest plus the SBOM hash.

## Tauri sidecar

Build a standalone `dt` binary and place it where Tauri expects it:

```bash
python3 -m pip install -e ".[crypto,sidecar]"
make sidecar
```

Then add this to `desktop-tauri/src-tauri/tauri.conf.json` under `bundle`:

```json
"externalBin": ["binaries/dt"]
```

and bundle with `make tauri-bundle`. The app still honors `DAYTHREAD_BIN` for
development.

## CI

`.github/workflows/release.yml` builds, tests, packages, and uploads `dist/` on
tags. Set the `DAYTHREAD_SIGNING_KEY` secret to a base64-encoded `release.pem`
to enable Ed25519 signing and attestation in CI.
