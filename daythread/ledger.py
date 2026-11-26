"""Append-only events and a hash-chained run ledger."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from .db import canonical, json_load, row_to_dict, utc_now


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def append_event(
    conn: sqlite3.Connection,
    source: str,
    kind: str,
    payload: Dict[str, Any],
    taint: str = "trusted",
) -> Dict[str, Any]:
    """Append an event to the global event chain."""
    ts = utc_now()
    last = conn.execute("SELECT hash FROM events ORDER BY rowid DESC LIMIT 1").fetchone()
    prev_hash = last["hash"] if last else ""
    body = {
        "ts": ts,
        "source": source,
        "type": kind,
        "payload": payload,
        "taint": taint,
        "prev_hash": prev_hash,
    }
    event_hash = _sha256(canonical(body))
    event_id = uuid4().hex
    conn.execute(
        """
        INSERT INTO events(id, ts, source, type, payload, taint, prev_hash, hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            ts,
            source,
            kind,
            canonical(payload),
            taint,
            prev_hash,
            event_hash,
        ),
    )
    conn.commit()
    return {
        "id": event_id,
        "ts": ts,
        "source": source,
        "type": kind,
        "taint": taint,
        "prev_hash": prev_hash,
        "hash": event_hash,
    }


def append_step(
    conn: sqlite3.Connection,
    run_id: str,
    kind: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Append a step to a run's hash chain."""
    created_at = utc_now()
    last = conn.execute(
        "SELECT seq, hash FROM run_steps WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    seq = 0 if last is None else int(last["seq"]) + 1
    prev_hash = "" if last is None else last["hash"]
    body = {
        "run_id": run_id,
        "seq": seq,
        "kind": kind,
        "payload": payload,
        "prev_hash": prev_hash,
        "created_at": created_at,
    }
    step_hash = _sha256(canonical(body))
    step_id = uuid4().hex
    conn.execute(
        """
        INSERT INTO run_steps(id, run_id, seq, kind, payload, prev_hash, hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            step_id,
            run_id,
            seq,
            kind,
            canonical(payload),
            prev_hash,
            step_hash,
            created_at,
        ),
    )
    conn.commit()
    return {
        "id": step_id,
        "run_id": run_id,
        "seq": seq,
        "kind": kind,
        "payload": payload,
        "prev_hash": prev_hash,
        "hash": step_hash,
        "created_at": created_at,
    }


def get_steps(conn: sqlite3.Connection, run_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM run_steps WHERE run_id = ? ORDER BY seq ASC", (run_id,)
    ).fetchall()
    steps = []
    for row in rows:
        item = row_to_dict(row)
        item["payload"] = json_load(item["payload"])
        steps.append(item)
    return steps


def verify_run(conn: sqlite3.Connection, run_id: str) -> Tuple[bool, List[str]]:
    """Recompute the chain; returns (ok, errors)."""
    errors: List[str] = []
    prev = ""
    for row in conn.execute(
        "SELECT * FROM run_steps WHERE run_id = ? ORDER BY seq ASC", (run_id,)
    ).fetchall():
        step = row_to_dict(row)
        if step["prev_hash"] != prev:
            errors.append(
                "step {}: prev_hash mismatch (expected {}, got {})".format(
                    step["seq"], prev, step["prev_hash"]
                )
            )
        body = {
            "run_id": step["run_id"],
            "seq": step["seq"],
            "kind": step["kind"],
            "payload": json_load(step["payload"]),
            "prev_hash": step["prev_hash"],
            "created_at": step["created_at"],
        }
        expected = _sha256(canonical(body))
        if expected != step["hash"]:
            errors.append("step {}: hash mismatch".format(step["seq"]))
        prev = step["hash"]
    return (not errors, errors)


def last_executed_step(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT * FROM run_steps
        WHERE run_id = ? AND kind = 'executed'
        ORDER BY seq DESC LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return {}
    item = row_to_dict(row)
    item["payload"] = json_load(item["payload"])
    return item
