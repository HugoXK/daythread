"""Build wheels, checksums, SBOM, and a signed release manifest."""

from __future__ import annotations

import argparse
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


def verify_manifest(manifest: Dict[str, Any], key: str) -> bool:
    signature = manifest.get("signature")
    if not signature:
        return False
    payload = {key_name: value for key_name, value in manifest.items() if key_name not in ("signature", "signature_alg")}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def write_release(dist_dir: Path, version: Optional[str] = None, key: Optional[str] = None) -> Dict[str, str]:
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
    manifest = build_manifest(dist_dir, version, commit, key)
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
        "signed": str(bool(key)).lower(),
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
    args = parser.parse_args(argv)
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
    summary = write_release(out, key=key)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
