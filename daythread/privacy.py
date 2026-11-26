"""DSAR-style subject export, forget, and tombstone purge."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from . import memory, obs
from .db import canonical, json_load, utc_now
from .errors import ValidationError


def export_subject(conn: sqlite3.Connection, subject_id: str) -> Dict[str, Any]:
    if not subject_id:
        raise ValidationError("subject_id is required")
    facts = memory.list_facts(
        conn,
        subject_id=subject_id,
        include_deleted=True,
        include_superseded=True,
        include_sensitive=True,
    )
    history: List[Dict[str, Any]] = []
    for fact in facts:
        history.extend(memory.history(conn, fact["id"]))
    return {
        "subject_id": subject_id,
        "exported_at": utc_now(),
        "facts": facts,
        "history": history,
    }


def forget_subject(
    conn: sqlite3.Connection,
    subject_id: str,
    hard: bool = False,
) -> Dict[str, int]:
    if not subject_id:
        raise ValidationError("subject_id is required")
    facts = memory.list_facts(
        conn,
        subject_id=subject_id,
        include_deleted=True,
        include_superseded=True,
        include_sensitive=True,
    )
    if not facts:
        return {"facts": 0, "history": 0, "events": 0}
    if not hard:
        count = 0
        for fact in facts:
            if not fact["deleted_at"]:
                memory.delete_fact(conn, fact["id"])
                count += 1
        obs.audit(
            conn,
            "privacy.forget",
            actor="user",
            target=subject_id,
            detail={"facts": count, "hard": False},
        )
        return {"facts": count, "history": 0, "events": 0}

    fact_ids = [fact["id"] for fact in facts]
    placeholders = ", ".join("?" for _ in fact_ids)
    history = conn.execute(
        "DELETE FROM fact_history WHERE fact_id IN ({})".format(placeholders),
        fact_ids,
    ).rowcount
    conn.execute("DELETE FROM facts WHERE subject_id = ?", (subject_id,))
    events = _delete_events_for_subject(conn, subject_id)
    conn.commit()
    obs.audit(
        conn,
        "privacy.forget",
        actor="user",
        target=subject_id,
        detail={"facts": len(fact_ids), "history": history, "events": events, "hard": True},
    )
    return {"facts": len(fact_ids), "history": history, "events": events}


def purge_tombstones(conn: sqlite3.Connection, older_than_days: int = 30) -> Dict[str, int]:
    if older_than_days < 0:
        raise ValidationError("older_than_days must be >= 0")
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=int(older_than_days))
    ).replace(microsecond=0).isoformat()
    rows = conn.execute(
        "SELECT id FROM facts WHERE deleted_at IS NOT NULL AND deleted_at < ?",
        (cutoff,),
    ).fetchall()
    ids = [row["id"] for row in rows]
    if not ids:
        return {"facts": 0, "history": 0}
    placeholders = ", ".join("?" for _ in ids)
    history = conn.execute(
        "DELETE FROM fact_history WHERE fact_id IN ({})".format(placeholders), ids
    ).rowcount
    conn.execute(
        "DELETE FROM facts WHERE id IN ({})".format(placeholders), ids
    )
    conn.commit()
    obs.audit(
        conn,
        "privacy.purge",
        actor="user",
        detail={"facts": len(ids), "history": history, "older_than_days": older_than_days},
    )
    return {"facts": len(ids), "history": history}


def _delete_events_for_subject(conn: sqlite3.Connection, subject_id: str) -> int:
    removed = 0
    for row in conn.execute("SELECT id, payload FROM events").fetchall():
        payload = json_load(row["payload"])
        if subject_id in canonical(payload):
            conn.execute("DELETE FROM events WHERE id = ?", (row["id"],))
            removed += 1
    return removed
