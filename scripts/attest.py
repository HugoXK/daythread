"""Build and verify a signed SLSA-style in-toto attestation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daythread.db import utc_now

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    HAVE_ED25519 = True
except Exception:  # noqa: BLE001
    HAVE_ED25519 = False

PAYLOAD_TYPE = "application/vnd.in-toto+json"
ROOT = Path(__file__).resolve().parent.parent


def build_statement(dist_dir: Path, commit: Optional[str] = None) -> Dict[str, Any]:
    dist_dir = Path(dist_dir)
    subjects = []
    for path in sorted(dist_dir.iterdir()):
        if not path.is_file() or path.name == "attestation.json":
            continue
        subjects.append(
            {
                "name": path.name,
                "digest": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
            }
        )
    materials = []
    sbom = dist_dir / "sbom.json"
    if sbom.exists():
        materials.append(
            {
                "uri": "sbom.json",
                "digest": {"sha256": hashlib.sha256(sbom.read_bytes()).hexdigest()},
            }
        )
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subjects,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://daythread.dev/release/v1",
                "externalParameters": {"command": "python scripts/release.py --build"},
                "resolvedDependencies": materials,
            },
            "runDetails": {
                "builder": {"id": "daythread-release"},
                "metadata": {
                    "invocationId": commit or "",
                    "startedOn": utc_now(),
                    "finishedOn": utc_now(),
                },
            },
        },
    }


def _pae(payload_type: str, payload: bytes) -> bytes:
    return b"DSSEv1 %d %s %d %s" % (
        len(payload_type),
        payload_type.encode("ascii"),
        len(payload),
        payload,
    )


def sign_statement(statement: Dict[str, Any], signing_key: Path) -> Dict[str, Any]:
    if not HAVE_ED25519:
        raise RuntimeError("cryptography is required for attestation signing")
    private = serialization.load_pem_private_key(
        Path(signing_key).read_bytes(), password=None
    )
    payload = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private.sign(_pae(PAYLOAD_TYPE, payload))
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": [
            {
                "keyid": base64.b64encode(public).decode("ascii"),
                "sig": base64.b64encode(signature).decode("ascii"),
            }
        ],
    }


def verify_envelope(envelope: Dict[str, Any]) -> bool:
    if not HAVE_ED25519 or envelope.get("payloadType") != PAYLOAD_TYPE:
        return False
    try:
        payload = base64.b64decode(envelope["payload"])
        public = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(envelope["signatures"][0]["keyid"])
        )
        signature = base64.b64decode(envelope["signatures"][0]["sig"])
        public.verify(signature, _pae(PAYLOAD_TYPE, payload))
        return True
    except Exception:  # noqa: BLE001
        return False


def write_attestation(dist_dir: Path, signing_key: Path, commit: Optional[str] = None) -> Path:
    statement = build_statement(dist_dir, commit)
    envelope = sign_statement(statement, signing_key)
    path = Path(dist_dir) / "attestation.json"
    path.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _git_commit() -> Optional[str]:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default=str(ROOT / "dist"))
    parser.add_argument("--signing-key", default=None)
    parser.add_argument("--verify", default=None)
    args = parser.parse_args(argv)
    if args.verify:
        envelope = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        print("attestation OK" if verify_envelope(envelope) else "attestation INVALID")
        return 0 if verify_envelope(envelope) else 1
    if not args.signing_key:
        raise SystemExit("--signing-key is required")
    path = write_attestation(Path(args.dist), Path(args.signing_key), _git_commit())
    print(json.dumps({"attestation": str(path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
