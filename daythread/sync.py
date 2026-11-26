"""Passphrase-encrypted sync bundles, file export, and relay transport."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote
from uuid import uuid4

from . import backup, crypto, migrations, obs
from .db import canonical, get_setting, set_setting, utc_now
from .errors import BackupError, DaythreadError, ProviderError

FORMAT = "daythread.sync.v1"


def build_bundle(conn, passphrase: str) -> Dict[str, Any]:
    if not passphrase:
        raise ValueError("passphrase is required")
    payload = backup.build_payload(conn)
    envelope = crypto.encrypt(passphrase, canonical(payload).encode("utf-8"))
    return {
        "format": FORMAT,
        "kdf": "scrypt",
        "cipher": "aes-256-gcm",
        "created_at": utc_now(),
        "schema_version": payload["schema_version"],
        **envelope,
    }


def decrypt_bundle(bundle: Dict[str, Any], passphrase: str) -> Dict[str, Any]:
    if not passphrase:
        raise ValueError("passphrase is required")
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


def export_sync(
    conn,
    home: Path,
    passphrase: str,
    out_dir: Optional[Path] = None,
) -> Path:
    bundle = build_bundle(conn, passphrase)
    directory = Path(out_dir) if out_dir else Path(home) / "sync"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / "daythread-sync-{}.json".format(stamp)
    path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    obs.audit(conn, "sync.exported", actor="user", target=str(path))
    return path


def decrypt_sync(path: Path, passphrase: str) -> Dict[str, Any]:
    path = Path(path)
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("cannot read sync bundle: {}".format(exc))
    return decrypt_bundle(bundle, passphrase)


def verify_sync(path: Path, passphrase: str) -> Dict[str, Any]:
    payload = decrypt_sync(path, passphrase)
    return {
        "format": payload["format"],
        "schema_version": payload["schema_version"],
        "created_at": payload["created_at"],
        "counts": payload.get("counts", {}),
    }


def import_bundle(
    conn,
    bundle: Dict[str, Any],
    passphrase: str,
    replace: bool = False,
) -> Dict[str, int]:
    payload = decrypt_bundle(bundle, passphrase)
    counts = backup.restore_payload(conn, payload, replace=replace)
    obs.audit(conn, "sync.imported", actor="user", detail=counts)
    return counts


def import_sync(
    conn,
    path: Path,
    passphrase: str,
    replace: bool = False,
) -> Dict[str, int]:
    path = Path(path)
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("cannot read sync bundle: {}".format(exc))
    return import_bundle(conn, bundle, passphrase, replace=replace)


def device_id(conn) -> str:
    value = get_setting(conn, "device.id", "") or ""
    if not value:
        value = uuid4().hex
        set_setting(conn, "device.id", value)
    return value


def push(
    conn,
    home: Path,
    passphrase: str,
    relay_url: str,
    relay_token: str,
    remote_device: Optional[str] = None,
    if_match: Optional[str] = None,
) -> Dict[str, Any]:
    target = remote_device or device_id(conn)
    bundle = build_bundle(conn, passphrase)
    result = _relay_request(
        "PUT",
        "{}/v1/bundles/{}".format(relay_url.rstrip("/"), quote(target, safe="")),
        relay_token,
        payload=bundle,
        headers={"If-Match": if_match} if if_match else None,
    )
    obs.audit(conn, "sync.pushed", actor="user", target=target)
    return result


def pull(
    conn,
    home: Path,
    passphrase: str,
    relay_url: str,
    relay_token: str,
    remote_device: Optional[str] = None,
    replace: bool = False,
) -> Dict[str, int]:
    target = remote_device or device_id(conn)
    bundle = _relay_request(
        "GET",
        "{}/v1/bundles/{}".format(relay_url.rstrip("/"), quote(target, safe="")),
        relay_token,
    )
    counts = import_bundle(conn, bundle, passphrase, replace=replace)
    obs.audit(conn, "sync.pulled", actor="user", target=target, detail=counts)
    return counts


def list_remote(relay_url: str, relay_token: str) -> Any:
    return _relay_request(
        "GET", "{}/v1/bundles".format(relay_url.rstrip("/")), relay_token
    )


def _relay_request(
    method: str,
    url: str,
    token: str,
    payload: Optional[Dict[str, Any]] = None,
    opener=None,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    if not token:
        raise ValueError("relay token is required")
    headers = {
        "Authorization": "Bearer {}".format(token),
        "Accept": "application/json",
        **(headers or {}),
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        if opener is not None:
            raw = opener(request)
        else:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        if hasattr(raw, "read"):
            raw = raw.read()
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise ProviderError("relay HTTP {}: {}".format(exc.code, detail))
    except Exception as exc:  # noqa: BLE001
        raise DaythreadError("relay request failed: {}".format(exc))
