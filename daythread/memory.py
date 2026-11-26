"""Living memory: structured facts, history, extraction, and tombstones."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .db import canonical, json_load, row_to_dict, utc_now
from .ledger import append_event

SUBJECT_TYPES = ("self", "person", "project", "org", "routine", "goal")
SENSITIVITIES = ("normal", "pii", "health", "secret")


def _row_to_fact(row: sqlite3.Row) -> Dict[str, Any]:
    fact = row_to_dict(row)
    fact["object"] = json_load(fact["object"])
    fact["provenance"] = json_load(fact["provenance"])
    fact["confidence"] = float(fact["confidence"])
    for key, default in (("tags", []), ("metadata", {}), ("score", 1.0)):
        if key in fact:
            fact[key] = json_load(fact[key]) if key != "score" else float(fact[key])
        else:
            fact[key] = default
    return fact


def _validate_common(subject_type: str, subject_id: str, predicate: str, confidence: float) -> None:
    if subject_type not in SUBJECT_TYPES:
        raise ValueError("subject_type must be one of {}".format(", ".join(SUBJECT_TYPES)))
    if not subject_id or not predicate:
        raise ValueError("subject_id and predicate are required")
    if not (0.0 <= float(confidence) <= 1.0):
        raise ValueError("confidence must be between 0 and 1")


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
    sensitivity: str = "normal",
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    supersedes: Optional[str] = None,
) -> Dict[str, Any]:
    _validate_common(subject_type, subject_id, predicate, confidence)
    if sensitivity not in SENSITIVITIES:
        raise ValueError("sensitivity must be one of {}".format(", ".join(SENSITIVITIES)))

    old: Optional[Dict[str, Any]] = None
    if supersedes:
        old = get_fact(conn, supersedes)
        if old["deleted_at"] or old["superseded_by"]:
            raise ValueError("cannot supersede an inactive fact: {}".format(supersedes))

    event = append_event(
        conn,
        source=source,
        kind="fact.added",
        payload={
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predicate": predicate,
            "value": value,
            "sensitivity": sensitivity,
            "supersedes": supersedes,
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
            deleted_at, updated_at, sensitivity, tags, metadata, score,
            last_accessed_at, supersedes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, ?, ?, ?, 1.0, ?, ?)
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
            sensitivity,
            canonical(list(tags or [])),
            canonical(dict(metadata or {})),
            now,
            supersedes,
        ),
    )
    conn.commit()
    fact = get_fact(conn, fact_id)

    if old is not None:
        conn.execute(
            "UPDATE facts SET superseded_by = ?, valid_to = ?, updated_at = ? WHERE id = ?",
            (fact_id, now, now, supersedes),
        )
        _append_history(conn, supersedes, event["id"], "supersede", old, fact)
    _append_history(conn, fact_id, event["id"], "add", None, fact)
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
    include_superseded: bool = False,
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
    if not include_superseded:
        clauses.append("superseded_by IS NULL")
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
    tags: Optional[List[str]] = None,
    sensitivity: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    before = get_fact(conn, fact_id)
    if before["deleted_at"]:
        raise ValueError("cannot edit a deleted fact")
    if sensitivity is not None and sensitivity not in SENSITIVITIES:
        raise ValueError("sensitivity must be one of {}".format(", ".join(SENSITIVITIES)))

    updates = []
    params: List[Any] = []
    if value is not None:
        updates.append("object = ?")
        params.append(canonical(value))
    if confidence is not None:
        new_confidence = float(confidence)
        if not (0.0 <= new_confidence <= 1.0):
            raise ValueError("confidence must be between 0 and 1")
        updates.append("confidence = ?")
        params.append(new_confidence)
    if tags is not None:
        updates.append("tags = ?")
        params.append(canonical(list(tags)))
    if sensitivity is not None:
        updates.append("sensitivity = ?")
        params.append(sensitivity)
    if metadata is not None:
        updates.append("metadata = ?")
        params.append(canonical(dict(metadata)))

    event = append_event(
        conn,
        source="user",
        kind="fact.updated",
        payload={"fact_id": fact_id, "fields": [item.split(" = ")[0] for item in updates]},
    )
    now = utc_now()
    updates.append("updated_at = ?")
    params.append(now)
    provenance = list(before["provenance"]) + [event["id"]]
    updates.append("provenance = ?")
    params.append(canonical(provenance))
    params.append(fact_id)
    conn.execute(
        "UPDATE facts SET {} WHERE id = ?".format(", ".join(updates)), params
    )
    conn.commit()
    after = get_fact(conn, fact_id)
    _append_history(conn, fact_id, event["id"], "update", before, after)
    conn.commit()
    return after


def delete_fact(conn: sqlite3.Connection, fact_id: str) -> Dict[str, Any]:
    fact = get_fact(conn, fact_id)
    if fact["deleted_at"]:
        return fact
    event = append_event(
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
    after = get_fact(conn, fact_id)
    _append_history(conn, fact_id, event["id"], "delete", fact, after)
    conn.commit()
    return after


def supersede_fact(
    conn: sqlite3.Connection,
    fact_id: str,
    value: Any,
    confidence: float = 0.9,
    source: str = "user",
) -> Dict[str, Any]:
    old = get_fact(conn, fact_id)
    return add_fact(
        conn,
        old["subject_type"],
        old["subject_id"],
        old["predicate"],
        value,
        confidence=confidence,
        source=source,
        supersedes=fact_id,
    )


def conflicts(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT subject_type, subject_id, predicate,
               COUNT(DISTINCT object) AS variants
        FROM facts
        WHERE deleted_at IS NULL AND superseded_by IS NULL
        GROUP BY subject_type, subject_id, predicate
        HAVING variants > 1
        ORDER BY variants DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def history(conn: sqlite3.Connection, fact_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM fact_history WHERE fact_id = ? ORDER BY ts ASC, rowid ASC", (fact_id,)
    ).fetchall()
    entries = []
    for row in rows:
        item = dict(row)
        item["before"] = json_load(item["before"]) if item["before"] else None
        item["after"] = json_load(item["after"]) if item["after"] else None
        entries.append(item)
    return entries


def search_facts(conn: sqlite3.Connection, query: str) -> List[Dict[str, Any]]:
    if not query.strip():
        return []
    like = "%{}%".format(query.strip())
    rows = conn.execute(
        """
        SELECT * FROM facts
        WHERE deleted_at IS NULL AND superseded_by IS NULL
          AND (subject_id LIKE ? OR predicate LIKE ? OR object LIKE ? OR tags LIKE ?)
        ORDER BY score DESC, updated_at DESC
        """,
        (like, like, like, like),
    ).fetchall()
    return [_row_to_fact(row) for row in rows]


def recent_facts(conn: sqlite3.Connection, limit: int = 5) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM facts
        WHERE deleted_at IS NULL AND superseded_by IS NULL
        ORDER BY updated_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_fact(row) for row in rows]


def touch(conn: sqlite3.Connection, fact_id: str) -> None:
    conn.execute(
        "UPDATE facts SET last_accessed_at = ?, score = MIN(score + 0.05, 2.0) WHERE id = ?",
        (utc_now(), fact_id),
    )
    conn.commit()


def decay(conn: sqlite3.Connection, half_life_days: float = 30.0) -> int:
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    now = datetime.now(timezone.utc)
    updated = 0
    for row in conn.execute(
        "SELECT id, score, updated_at FROM facts WHERE deleted_at IS NULL"
    ).fetchall():
        try:
            age = (now - datetime.fromisoformat(row["updated_at"])).total_seconds() / 86400.0
        except (TypeError, ValueError):
            continue
        factor = 0.5 ** (max(age, 0.0) / half_life_days)
        conn.execute(
            "UPDATE facts SET score = ? WHERE id = ?",
            (max(float(row["score"]) * factor, 0.01), row["id"]),
        )
        updated += 1
    conn.commit()
    return updated


_PATTERNS = (
    ("person", re.compile(r"(?P<name>[A-Z][A-Za-z'\-]+) is my (?P<role>[\w\-\s]+?)(?:[.,;]|$)", re.I)),
    ("preference", re.compile(r"\bI prefer (?P<pref>.+?)(?:[.;]|$)", re.I)),
    ("deadline", re.compile(r"\bdeadline(?: is)? (?P<date>\d{4}-\d{2}-\d{2})", re.I)),
    ("note", re.compile(r"\bremember(?: that)? (?P<note>.+?)(?:[.;]|$)", re.I)),
)


def extract_facts(text: str) -> List[Dict[str, Any]]:
    """Deterministic, conservative extraction used before an LLM worker exists."""
    if not text or not text.strip():
        return []
    found: Dict[tuple, Dict[str, Any]] = {}

    def add(subject_type, subject_id, predicate, value, confidence, span):
        key = (subject_type, subject_id, predicate, str(value))
        if key in found:
            return
        found[key] = {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predicate": predicate,
            "value": str(value).strip(),
            "confidence": confidence,
            "span": span,
        }

    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if kind == "person":
                name = match.group("name").strip()
                add("person", name.lower(), "role", match.group("role").strip(), 0.7, match.group(0))
            elif kind == "preference":
                add("self", "self", "preference", match.group("pref").strip(), 0.7, match.group(0))
            elif kind == "deadline":
                add("self", "self", "deadline", match.group("date").strip(), 0.75, match.group(0))
            elif kind == "note":
                add("self", "self", "note", match.group("note").strip(), 0.55, match.group(0))
    return list(found.values())


def apply_extracted(
    conn: sqlite3.Connection,
    candidates: List[Dict[str, Any]],
    created_by: str = "extractor",
) -> List[Dict[str, Any]]:
    facts = []
    for candidate in candidates:
        facts.append(
            add_fact(
                conn,
                candidate["subject_type"],
                candidate["subject_id"],
                candidate["predicate"],
                candidate["value"],
                confidence=float(candidate.get("confidence", 0.6)),
                source="extractor",
                created_by=created_by,
                metadata={"span": candidate.get("span", "")},
            )
        )
    return facts


def import_json(conn: sqlite3.Connection, data: Dict[str, Any]) -> int:
    count = 0
    for item in data.get("facts", []):
        if item.get("deleted_at"):
            continue
        add_fact(
            conn,
            item["subject_type"],
            item["subject_id"],
            item["predicate"],
            item.get("object"),
            confidence=float(item.get("confidence", 0.8)),
            source="import",
            created_by=item.get("created_by", "import"),
            sensitivity=item.get("sensitivity", "normal"),
            tags=item.get("tags") or [],
            metadata=item.get("metadata") or {},
        )
        count += 1
    return count


def _append_history(
    conn: sqlite3.Connection,
    fact_id: str,
    event_id: Optional[str],
    action: str,
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
) -> None:
    conn.execute(
        """
        INSERT INTO fact_history(id, fact_id, event_id, action, before, after, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex,
            fact_id,
            event_id,
            action,
            canonical(before) if before is not None else None,
            canonical(after) if after is not None else None,
            utc_now(),
        ),
    )
