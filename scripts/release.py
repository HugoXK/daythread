"""Build wheels, checksums, SBOM, and a signed release manifest."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daythread import __version__
from daythread.db import utc_now

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    HAVE_ED25519 = True
except Exception:  # noqa: BLE001 - optional signing dependency
    HAVE_ED25519 = False

ROOT = Path(__file__).resolve().parent.parent


def build_sbom(version: Optional[str] = None, files: Optional[Iterable[Path]] = None) -> Dict[str, Any]:
    version = version or __version__
    components = []
    try:
        crypto_version = metadata.version("cryptography")
    except metadata.PackageNotFoundError:
        crypto_version = None
    if crypto_version:
        components.append(
            {
                "type": "library",
                "name": "cryptography",
                "version": crypto_version,
                "scope": "optional",
            }
        )
    file_entries = []
    for path in files or []:
        data = Path(path).read_bytes()
        file_entries.append(
            {
                "name": Path(path).name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:{}".format(uuid4()),
        "version": 1,
        "metadata": {
            "timestamp": utc_now(),
            "component": {
                "type": "application",
                "name": "daythread",
                "version": version,
            },
        },
        "components": components,
        "properties": [
            {"name": "daythread:python", "value": sys.version.split()[0]},
            {"name": "daythread:files", "value": json.dumps(file_entries)},
        ],
    }


def build_manifest(
    dist_dir: Path,
    version: Optional[str] = None,
    commit: Optional[str] = None,
    key: Optional[str] = None,
) -> Dict[str, Any]:
    version = version or __version__
    files = []
    for path in sorted(Path(dist_dir).iterdir()):
        if not path.is_file() or path.name in (
            "release-manifest.json",
            "SHA256SUMS",
            "sbom.json",
        ):
            continue
        data = path.read_bytes()
        files.append(
            {
                "name": path.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    manifest = {
        "name": "daythread",
        "version": version,
        "commit": commit,
        "created_at": utc_now(),
        "files": files,
    }
    if key:
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        manifest["signature_alg"] = "hmac-sha256"
        manifest["signature"] = hmac.new(
            key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    return manifest


def generate_signing_key(path: Path) -> Path:
    if not HAVE_ED25519:
        raise RuntimeError("cryptography is required for Ed25519 signing")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    data = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def sign_manifest_ed25519(manifest: Dict[str, Any], signing_key: Path) -> Dict[str, Any]:
    if not HAVE_ED25519:
        raise RuntimeError("cryptography is required for Ed25519 signing")
    private = serialization.load_pem_private_key(Path(signing_key).read_bytes(), password=None)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in ("signature", "signature_alg", "public_key")
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    signature = private.sign(canonical.encode("utf-8"))
    signed = dict(payload)
    signed["signature_alg"] = "ed25519"
    signed["signature"] = base64.b64encode(signature).decode("ascii")
    signed["public_key"] = base64.b64encode(public).decode("ascii")
    return signed


def verify_manifest_ed25519(manifest: Dict[str, Any]) -> bool:
    if not HAVE_ED25519 or manifest.get("signature_alg") != "ed25519":
        return False
    try:
        public = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(manifest["public_key"])
        )
        signature = base64.b64decode(manifest["signature"])
        payload = {
            key: value
            for key, value in manifest.items()
            if key not in ("signature", "signature_alg", "public_key")
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        public.verify(signature, canonical.encode("utf-8"))
        return True
    except Exception:  # noqa: BLE001 - invalid signature
        return False


def verify_manifest(manifest: Dict[str, Any], key: str) -> bool:
    signature = manifest.get("signature")
    if not signature:
        return False
    payload = {key_name: value for key_name, value in manifest.items() if key_name not in ("signature", "signature_alg")}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def write_release(
    dist_dir: Path,
    version: Optional[str] = None,
    key: Optional[str] = None,
    signing_key: Optional[Path] = None,
) -> Dict[str, str]:
    dist_dir = Path(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    version = version or __version__
    commit = _git_commit()
    files = [
        path
        for path in dist_dir.iterdir()
        if path.is_file()
        and path.name not in ("release-manifest.json", "SHA256SUMS", "sbom.json")
    ]
    sbom = build_sbom(version, files)
    (dist_dir / "sbom.json").write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest = build_manifest(dist_dir, version, commit, key=None if signing_key else key)
    if signing_key:
        manifest = sign_manifest_ed25519(manifest, signing_key)
    (dist_dir / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    lines = []
    for item in manifest["files"]:
        lines.append("{}  {}".format(item["sha256"], item["name"]))
    (dist_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "version": version,
        "dist": str(dist_dir),
        "signed": str(bool(key or signing_key)).lower(),
        "signature_alg": manifest.get("signature_alg", "hmac-sha256" if key else None),
        "files": str(len(manifest["files"])),
    }


def _git_commit() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() or None
    except OSError:
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--key-env", default="DAYTHREAD_RELEASE_KEY")
    parser.add_argument("--signing-key", default=None)
    parser.add_argument("--generate-key", default=None)
    args = parser.parse_args(argv)
    if args.generate_key:
        path = generate_signing_key(Path(args.generate_key))
        print(json.dumps({"signing_key": str(path)}, sort_keys=True))
        return 0
    out = Path(args.out)
    if args.build:
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                str(ROOT),
                "-w",
                str(out),
                "--no-deps",
                "--no-build-isolation",
            ],
            check=True,
        )
    key = os.environ.get(args.key_env)
    summary = write_release(
        out,
        key=key,
        signing_key=Path(args.signing_key) if args.signing_key else None,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
