"""Versioned, checksummed local backup and restore."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import crypto, migrations, obs
from .db import canonical, row_to_dict, utc_now
from .errors import BackupError

FORMAT = "daythread.backup.v1"
ENC_FORMAT = "daythread.backup.enc.v1"
TABLES = (
    "facts",
    "fact_history",
    "tasks",
    "calendar_events",
    "messages",
    "candidates",
    "signals",
    "connectors",
    "skills",
    "settings",
    "mcp_servers",
    "events",
    "runs",
    "run_steps",
    "briefings",
    "notifications",
    "scheduler_runs",
    "audit",
    "model_usage",
    "idempotency",
)
SENSITIVE_SETTING_MARKERS = ("token", "secret", "password", "api_key", "cookie")


def build_payload(conn: sqlite3.Connection) -> Dict[str, Any]:
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for table in TABLES:
        rows = [row_to_dict(row) for row in conn.execute("SELECT * FROM {}".format(table))]
        if table == "connectors":
            for row in rows:
                row["token_ref"] = None
        if table == "settings":
            rows = [row for row in rows if not _sensitive(row["key"])]
        if table == "mcp_servers":
            for row in rows:
                row["env"] = _redact_env(row.get("env", "{}"))
        tables[table] = rows
    return {
        "format": FORMAT,
        "schema_version": migrations.current_version(conn),
        "created_at": utc_now(),
        "tables": tables,
        "counts": {name: len(rows) for name, rows in tables.items()},
    }


def create_backup(
    conn: sqlite3.Connection,
    home: Path,
    out_dir: Optional[Path] = None,
    passphrase: Optional[str] = None,
) -> Path:
    directory = Path(out_dir) if out_dir else Path(home) / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    data = build_payload(conn)
    if passphrase:
        envelope = crypto.encrypt(passphrase, canonical(data).encode("utf-8"))
        bundle = {
            "format": ENC_FORMAT,
            "created_at": utc_now(),
            **envelope,
        }
    else:
        checksum = _checksum(data)
        bundle = {"format": FORMAT, "checksum": checksum, "data": data}
        if crypto.HAVE_CRYPTO:
            bundle["signature_alg"] = "hmac-sha256"
            bundle["signature"] = _signature(data, home)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / "daythread-{}.json".format(stamp)
    path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    obs.audit(conn, "backup.created", actor="user", target=str(path), detail=data["counts"])
    return path


def load_backup(
    path: Path,
    home: Optional[Path] = None,
    verify_signature: bool = True,
    passphrase: Optional[str] = None,
) -> Dict[str, Any]:
    path = Path(path)
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("cannot read backup: {}".format(exc))
    if bundle.get("format") == ENC_FORMAT:
        if not passphrase:
            raise BackupError("encrypted backup requires a passphrase")
        plaintext = crypto.decrypt(passphrase, bundle)
        try:
            data = json.loads(plaintext.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BackupError("encrypted backup payload is invalid")
    elif bundle.get("format") != FORMAT:
        raise BackupError("unsupported backup format")
    else:
        data = bundle.get("data")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("invalid backup payload")
    if bundle.get("format") == FORMAT and bundle.get("checksum") != _checksum(data):
        raise BackupError("backup checksum mismatch")
    signature = bundle.get("signature")
    if signature and verify_signature:
        if not crypto.HAVE_CRYPTO:
            raise BackupError("backup is signed but cryptography is not installed")
        if home is None:
            raise BackupError("backup is signed; provide the daythread home to verify")
        if not hmac.compare_digest(signature, _signature(data, Path(home))):
            raise BackupError("backup signature mismatch")
    if int(data.get("schema_version", 0)) > migrations.LATEST_VERSION:
        raise BackupError("backup was created by a newer schema")
    return data


def restore_backup(
    conn: sqlite3.Connection,
    path: Path,
    replace: bool = False,
    home: Optional[Path] = None,
    verify_signature: bool = True,
    passphrase: Optional[str] = None,
) -> Dict[str, int]:
    return restore_payload(
        conn,
        load_backup(
            path,
            home=home,
            verify_signature=verify_signature,
            passphrase=passphrase,
        ),
        replace=replace,
    )


def restore_payload(
    conn: sqlite3.Connection,
    data: Dict[str, Any],
    replace: bool = False,
) -> Dict[str, int]:
    if data.get("format") != FORMAT:
        raise BackupError("invalid backup payload")
    if int(data.get("schema_version", 0)) > migrations.LATEST_VERSION:
        raise BackupError("backup was created by a newer schema")
    tables = data.get("tables", {})
    counts: Dict[str, int] = {}
    statement = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    for table in TABLES:
        rows = tables.get(table, [])
        if not isinstance(rows, list):
            raise BackupError("table {} is malformed".format(table))
        if table == "settings":
            rows = [row for row in rows if not _sensitive(str(row.get("key", "")))]
        if table == "mcp_servers":
            rows = [_restore_env(row) for row in rows]
        counts[table] = _insert_rows(conn, table, rows, statement)
    conn.commit()
    obs.audit(conn, "backup.restored", actor="user", target=data.get("created_at"), detail=counts)
    return counts


def verify_backup(
    path: Path,
    home: Optional[Path] = None,
    passphrase: Optional[str] = None,
) -> Dict[str, Any]:
    data = load_backup(path, home=home, passphrase=passphrase)
    return {
        "format": data["format"],
        "schema_version": data["schema_version"],
        "created_at": data["created_at"],
        "counts": data.get("counts", {}),
        "encrypted": passphrase is not None,
    }


def _insert_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: List[Dict[str, Any]],
    statement: str,
) -> int:
    if not rows:
        return 0
    available = {row["name"] for row in conn.execute("PRAGMA table_info({})".format(table))}
    inserted = 0
    for row in rows:
        if not isinstance(row, dict):
            raise BackupError("row in {} is not an object".format(table))
        columns = [column for column in row if column in available]
        if not columns:
            continue
        placeholders = ", ".join("?" for _ in columns)
        sql = "{} INTO {} ({}) VALUES ({})".format(
            statement,
            table,
            ", ".join(columns),
            placeholders,
        )
        cursor = conn.execute(sql, [row[column] for column in columns])
        if cursor.rowcount:
            inserted += 1
    return inserted


def _checksum(data: Dict[str, Any]) -> str:
    return sha256(canonical(data).encode("utf-8")).hexdigest()


def _signature(data: Dict[str, Any], home: Path) -> str:
    key = crypto.load_or_create_master_key(Path(home))
    return hmac.new(
        key, canonical(data).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_SETTING_MARKERS)


def _redact_env(raw: str) -> str:
    try:
        env = json.loads(raw)
    except (TypeError, ValueError):
        return "{}"
    redacted = {}
    for key, value in env.items():
        if isinstance(value, str) and (
            value.startswith("env:") or value.startswith("secret:")
        ):
            redacted[key] = value
        else:
            redacted[key] = "[REDACTED]"
    return canonical(redacted)


def _restore_env(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    try:
        env = json.loads(item.get("env", "{}"))
    except (TypeError, ValueError):
        env = {}
    item["env"] = canonical(
        {key: value for key, value in env.items() if value != "[REDACTED]"}
    )
    return item
