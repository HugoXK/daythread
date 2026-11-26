"""Passphrase-encrypted file sync built on the backup payload."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from . import backup, crypto, migrations, obs
from .db import canonical, utc_now
from .errors import BackupError

FORMAT = "daythread.sync.v1"


def export_sync(
    conn,
    home: Path,
    passphrase: str,
    out_dir: Optional[Path] = None,
) -> Path:
    if not passphrase:
        raise ValueError("passphrase is required")
    payload = backup.build_payload(conn)
    plaintext = canonical(payload).encode("utf-8")
    envelope = crypto.encrypt(passphrase, plaintext)
    bundle = {
        "format": FORMAT,
        "kdf": "scrypt",
        "cipher": "aes-256-gcm",
        "created_at": utc_now(),
        "schema_version": payload["schema_version"],
        **envelope,
    }
    directory = Path(out_dir) if out_dir else Path(home) / "sync"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / "daythread-sync-{}.json".format(stamp)
    path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    obs.audit(conn, "sync.exported", actor="user", target=str(path), detail=payload["counts"])
    return path


def decrypt_sync(path: Path, passphrase: str) -> Dict[str, Any]:
    if not passphrase:
        raise ValueError("passphrase is required")
    path = Path(path)
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("cannot read sync bundle: {}".format(exc))
    if bundle.get("format") != FORMAT:
        raise BackupError("unsupported sync format")
    plaintext = crypto.decrypt(passphrase, bundle)
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BackupError("sync payload is not valid JSON")
    if not isinstance(payload, dict) or payload.get("format") != backup.FORMAT:
        raise BackupError("invalid sync payload")
    if int(payload.get("schema_version", 0)) > migrations.LATEST_VERSION:
        raise BackupError("sync bundle was created by a newer schema")
    return payload


def verify_sync(path: Path, passphrase: str) -> Dict[str, Any]:
    payload = decrypt_sync(path, passphrase)
    return {
        "format": payload["format"],
        "schema_version": payload["schema_version"],
        "created_at": payload["created_at"],
        "counts": payload.get("counts", {}),
    }


def import_sync(
    conn,
    path: Path,
    passphrase: str,
    replace: bool = False,
) -> Dict[str, int]:
    payload = decrypt_sync(path, passphrase)
    counts = backup.restore_payload(conn, payload, replace=replace)
    obs.audit(conn, "sync.imported", actor="user", target=str(path), detail=counts)
    return counts
