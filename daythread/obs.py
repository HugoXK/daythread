"""Audit log, metrics, and structured logging."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from typing import Any, Dict, Optional
from uuid import uuid4

from .db import canonical, utc_now

SENSITIVE_KEYS = ("token", "secret", "password", "authorization", "api_key", "cookie")


def new_trace_id() -> str:
    return uuid4().hex


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if any(marker in str(key).lower() for marker in SENSITIVE_KEYS) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def audit(
    conn: sqlite3.Connection,
    action: str,
    actor: str = "user",
    target: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> str:
    audit_id = uuid4().hex
    conn.execute(
        "INSERT INTO audit(id, ts, actor, action, target, detail, trace_id) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            audit_id,
            utc_now(),
            actor,
            action,
            target,
            canonical(redact(detail or {})),
            trace_id,
        ),
    )
    conn.commit()
    return audit_id


def audit_history(conn: sqlite3.Connection, limit: int = 50) -> list:
    rows = conn.execute(
        "SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def incr(conn: sqlite3.Connection, name: str, value: float = 1.0) -> float:
    row = conn.execute("SELECT value FROM metrics WHERE name = ?", (name,)).fetchone()
    new_value = (row["value"] if row else 0.0) + float(value)
    conn.execute(
        "INSERT OR REPLACE INTO metrics(name, value, updated_at) VALUES(?, ?, ?)",
        (name, new_value, utc_now()),
    )
    conn.commit()
    return new_value


def set_metric(conn: sqlite3.Connection, name: str, value: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO metrics(name, value, updated_at) VALUES(?, ?, ?)",
        (name, float(value), utc_now()),
    )
    conn.commit()


def snapshot(conn: sqlite3.Connection) -> Dict[str, float]:
    return {
        row["name"]: float(row["value"])
        for row in conn.execute("SELECT name, value FROM metrics ORDER BY name")
    }


_configured = False


def configure_logging(level: Optional[str] = None) -> None:
    global _configured
    if _configured:
        return
    level_name = (level or os.environ.get("DAYTHREAD_LOG_LEVEL", "WARNING")).upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("daythread")
    root.handlers = [handler]
    root.setLevel(getattr(logging, level_name, logging.WARNING))
    root.propagate = False
    _configured = True


def get_logger(name: str = "daythread") -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: str, event: str, **fields: Any) -> None:
    payload = {"event": event}
    payload.update(redact(fields))
    getattr(logger, level.lower(), logger.info)(json.dumps(payload, ensure_ascii=False))
