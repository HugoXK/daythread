"""Signed webhook ingestion with replay protection and dedupe."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from . import obs, proactivity
from .crypto import SecretVault
from .db import canonical, json_load, row_to_dict, utc_now
from .errors import ValidationError

SIGNATURE_HEADER = "X-Daythread-Signature"
TIMESTAMP_HEADER = "X-Daythread-Timestamp"
DEFAULT_MAX_AGE = 300


def set_secret(conn, home: Path, source: str, secret: str) -> None:
    if not source or not secret:
        raise ValidationError("source and secret are required")
    SecretVault(conn, Path(home)).set(_secret_name(source), secret)
    obs.audit(conn, "webhook.secret.set", actor="user", target=source)


def get_secret(conn, home: Path, source: str) -> str:
    return SecretVault(conn, Path(home)).get(_secret_name(source))


def delete_secret(conn, home: Path, source: str) -> None:
    SecretVault(conn, Path(home)).delete(_secret_name(source))
    obs.audit(conn, "webhook.secret.deleted", actor="user", target=source)


def sign(secret: str, body: bytes, timestamp: Optional[int] = None) -> Dict[str, str]:
    moment = str(int(timestamp if timestamp is not None else time.time()))
    message = moment.encode("ascii") + b"." + body
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return {"timestamp": moment, "signature": "sha256=" + signature}


def verify(
    secret: str,
    body: bytes,
    timestamp: Optional[str],
    signature: Optional[str],
    max_age: int = DEFAULT_MAX_AGE,
) -> bool:
    if not timestamp or not signature:
        return False
    try:
        moment = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - moment) > max_age:
        return False
    expected = sign(secret, body, timestamp=moment)["signature"]
    return hmac.compare_digest(signature, expected)


def ingest(
    conn,
    source: str,
    payload: Dict[str, Any],
    dedupe_key: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")
    event_id = uuid4().hex
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO webhook_events(id, source, dedupe_key, payload, ts, status)
        VALUES(?, ?, ?, ?, ?, 'accepted')
        """,
        (event_id, source, dedupe_key, canonical(payload), utc_now()),
    )
    conn.commit()
    if cursor.rowcount == 0:
        row = conn.execute(
            "SELECT * FROM webhook_events WHERE source = ? AND dedupe_key = ?",
            (source, dedupe_key),
        ).fetchone()
        return {
            "duplicate": True,
            "event_id": row["id"] if row else None,
            "signal_id": None,
        }
    signal = proactivity.ingest_signal(
        conn,
        source="webhook:{}".format(source),
        kind="event",
        payload=payload,
        dedupe_key=dedupe_key,
    )
    obs.incr(conn, "webhooks.ingested")
    obs.audit(
        conn,
        "webhook.ingested",
        actor="webhook",
        target=source,
        detail={"event_id": event_id, "signal_id": signal["id"]},
    )
    return {"duplicate": False, "event_id": event_id, "signal_id": signal["id"]}


def list_events(conn, limit: int = 50) -> List[Dict[str, Any]]:
    items = []
    for row in conn.execute(
        "SELECT * FROM webhook_events ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall():
        item = row_to_dict(row)
        item["payload"] = json_load(item["payload"])
        items.append(item)
    return items


def _secret_name(source: str) -> str:
    return "webhook.{}".format(source)
