"""Living memory: structured facts with provenance and tombstones."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .db import canonical, json_load, row_to_dict, utc_now
from .ledger import append_event

SUBJECT_TYPES = ("self", "person", "project", "org", "routine", "goal")


def _row_to_fact(row: sqlite3.Row) -> Dict[str, Any]:
    fact = row_to_dict(row)
    fact["object"] = json_load(fact["object"])
    fact["provenance"] = json_load(fact["provenance"])
    fact["confidence"] = float(fact["confidence"])
    return fact


def add_fact(
    conn: sqlite3.Connection,
    subject_type: str,
    subject_id: str,
    predicate: str,
    value: Any,
    confidence: float = 0.8,
    source: str = "user",
    created_by: str = "user",
    valid_from: Optional[str] = None,
    taint: str = "trusted",
) -> Dict[str, Any]:
    if not subject_type or not subject_id or not predicate:
        raise ValueError("subject_type, subject_id and predicate are required")
    if not (0.0 <= float(confidence) <= 1.0):
        raise ValueError("confidence must be between 0 and 1")

    event = append_event(
        conn,
        source=source,
        kind="fact.added",
        payload={
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predicate": predicate,
            "value": value,
        },
        taint=taint,
    )
    fact_id = uuid4().hex
    now = utc_now()
    conn.execute(
        """
        INSERT INTO facts(
            id, subject_type, subject_id, predicate, object, confidence,
            provenance, valid_from, valid_to, superseded_by, created_by,
            deleted_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?)
        """,
        (
            fact_id,
            subject_type,
            subject_id,
            predicate,
            canonical(value),
            float(confidence),
            canonical([event["id"]]),
            valid_from or now,
            created_by,
            now,
        ),
    )
    conn.commit()
    return get_fact(conn, fact_id)


def get_fact(conn: sqlite3.Connection, fact_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        raise KeyError("fact not found: {}".format(fact_id))
    return _row_to_fact(row)


def list_facts(
    conn: sqlite3.Connection,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    clauses = []
    params: List[Any] = []
    if subject_type:
        clauses.append("subject_type = ?")
        params.append(subject_type)
    if subject_id:
        clauses.append("subject_id = ?")
        params.append(subject_id)
    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        "SELECT * FROM facts{} ORDER BY updated_at DESC".format(where), params
    ).fetchall()
    return [_row_to_fact(row) for row in rows]


def edit_fact(
    conn: sqlite3.Connection,
    fact_id: str,
    value: Any = None,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    fact = get_fact(conn, fact_id)
    if fact["deleted_at"]:
        raise ValueError("cannot edit a deleted fact")
    new_value = fact["object"] if value is None else value
    new_confidence = fact["confidence"] if confidence is None else float(confidence)
    if not (0.0 <= new_confidence <= 1.0):
        raise ValueError("confidence must be between 0 and 1")

    event = append_event(
        conn,
        source="user",
        kind="fact.updated",
        payload={"fact_id": fact_id, "value": new_value, "confidence": new_confidence},
    )
    provenance = list(fact["provenance"]) + [event["id"]]
    conn.execute(
        "UPDATE facts SET object = ?, confidence = ?, provenance = ?, updated_at = ? WHERE id = ?",
        (
            canonical(new_value),
            new_confidence,
            canonical(provenance),
            utc_now(),
            fact_id,
        ),
    )
    conn.commit()
    return get_fact(conn, fact_id)


def delete_fact(conn: sqlite3.Connection, fact_id: str) -> Dict[str, Any]:
    fact = get_fact(conn, fact_id)
    if fact["deleted_at"]:
        return fact
    append_event(
        conn,
        source="user",
        kind="fact.deleted",
        payload={"fact_id": fact_id},
    )
    now = utc_now()
    conn.execute(
        "UPDATE facts SET deleted_at = ?, updated_at = ? WHERE id = ?",
        (now, now, fact_id),
    )
    conn.commit()
    return get_fact(conn, fact_id)


def search_facts(conn: sqlite3.Connection, query: str) -> List[Dict[str, Any]]:
    if not query.strip():
        return []
    like = "%{}%".format(query.strip())
    rows = conn.execute(
        """
        SELECT * FROM facts
        WHERE deleted_at IS NULL
          AND (subject_id LIKE ? OR predicate LIKE ? OR object LIKE ?)
        ORDER BY updated_at DESC
        """,
        (like, like, like),
    ).fetchall()
    return [_row_to_fact(row) for row in rows]


def recent_facts(conn: sqlite3.Connection, limit: int = 5) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM facts WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_fact(row) for row in rows]
