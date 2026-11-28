"""Signals, candidates, interrupt budget, and feedback."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from . import obs
from .db import canonical, get_setting, json_load, row_to_dict, utc_now

DEFAULTS = {
    "proactivity.max_per_day": "3",
    "proactivity.min_score": "0.35",
    "proactivity.quiet_start": "22",
    "proactivity.quiet_end": "7",
}


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _setting(conn: sqlite3.Connection, key: str) -> str:
    return str(get_setting(conn, key, DEFAULTS[key]))


def ingest_signal(
    conn: sqlite3.Connection,
    source: str,
    kind: str,
    payload: Dict[str, Any],
    dedupe_key: Optional[str] = None,
    taint: str = "trusted",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    moment = _now(now)
    if dedupe_key:
        window = (moment - timedelta(hours=24)).isoformat()
        existing = conn.execute(
            "SELECT * FROM signals WHERE source = ? AND kind = ? AND dedupe_key = ? AND taint = ? AND ts >= ? ORDER BY ts DESC LIMIT 1",
            (source, kind, dedupe_key, taint, window),
        ).fetchone()
        if existing:
            item = row_to_dict(existing)
            item["payload"] = json_load(item["payload"])
            return item
    signal_id = uuid4().hex
    conn.execute(
        "INSERT INTO signals(id, source, kind, payload, ts, taint, dedupe_key, processed) VALUES(?, ?, ?, ?, ?, ?, ?, 0)",
        (signal_id, source, kind, canonical(payload), moment.isoformat(), taint, dedupe_key),
    )
    conn.commit()
    obs.incr(conn, "signals.ingested")
    return {
        "id": signal_id,
        "source": source,
        "kind": kind,
        "payload": payload,
        "ts": moment.isoformat(),
        "taint": taint,
        "dedupe_key": dedupe_key,
        "processed": 0,
    }


def list_signals(conn: sqlite3.Connection, unprocessed: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
    where = " WHERE processed = 0" if unprocessed else ""
    rows = conn.execute(
        "SELECT * FROM signals{} ORDER BY ts DESC LIMIT ?".format(where), (limit,)
    ).fetchall()
    items = []
    for row in rows:
        item = row_to_dict(row)
        item["payload"] = json_load(item["payload"])
        items.append(item)
    return items


def ensure_budget(conn: sqlite3.Connection, now: Optional[datetime] = None) -> Dict[str, Any]:
    day = _now(now).date().isoformat()
    max_sent = int(_setting(conn, "proactivity.max_per_day"))
    conn.execute(
        "INSERT OR IGNORE INTO budgets(date, sent, max_sent) VALUES(?, 0, ?)",
        (day, max_sent),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM budgets WHERE date = ?", (day,)).fetchone()
    return dict(row)


def record_interrupt(conn: sqlite3.Connection, now: Optional[datetime] = None) -> Dict[str, Any]:
    ensure_budget(conn, now)
    day = _now(now).date().isoformat()
    conn.execute("UPDATE budgets SET sent = sent + 1 WHERE date = ?", (day,))
    conn.commit()
    obs.incr(conn, "proactivity.interrupts")
    row = conn.execute("SELECT * FROM budgets WHERE date = ?", (day,)).fetchone()
    return dict(row)


def can_interrupt(conn: sqlite3.Connection, now: Optional[datetime] = None) -> bool:
    moment = _now(now)
    budget = ensure_budget(conn, moment)
    if budget["sent"] >= budget["max_sent"]:
        return False
    start = int(_setting(conn, "proactivity.quiet_start"))
    end = int(_setting(conn, "proactivity.quiet_end"))
    hour = moment.hour
    if start != end:
        if start > end:
            quiet = hour >= start or hour < end
        else:
            quiet = start <= hour < end
        if quiet:
            return False
    return True


def generate_candidates(
    conn: sqlite3.Connection, now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    moment = _now(now)
    day = moment.date().isoformat()
    created: List[Dict[str, Any]] = []

    for run in conn.execute(
        "SELECT * FROM runs WHERE state = 'pending_approval' ORDER BY created_at"
    ).fetchall():
        run_dict = dict(run)
        created.append(
            _candidate(
                conn,
                kind="approval",
                title="Approval needed: {}".format(run_dict["tool"]),
                body={"run_id": run_dict["id"], "risk": run_dict["risk"]},
                relevance=1.0,
                urgency=1.0,
                actionability=0.8,
                novelty=0.9,
                now=moment,
            )
        )

    for task in conn.execute(
        "SELECT * FROM tasks WHERE status = 'open' AND due IS NOT NULL AND date(due) <= ?",
        (day,),
    ).fetchall():
        task_dict = dict(task)
        created.append(
            _candidate(
                conn,
                kind="task",
                title="Task due: {}".format(task_dict["title"]),
                body={"task_id": task_dict["id"], "due": task_dict["due"]},
                relevance=0.9,
                urgency=0.8,
                actionability=0.7,
                novelty=0.6,
                now=moment,
            )
        )

    for fact in conn.execute(
        """
        SELECT * FROM facts
        WHERE deleted_at IS NULL AND superseded_by IS NULL
          AND predicate IN ('commitment', 'promise', 'deadline')
        ORDER BY updated_at DESC LIMIT 10
        """
    ).fetchall():
        fact_dict = dict(fact)
        created.append(
            _candidate(
                conn,
                kind="commitment",
                title="Commitment: {} · {}".format(fact_dict["subject_id"], fact_dict["predicate"]),
                body={"fact_id": fact_dict["id"], "value": json_load(fact_dict["object"])},
                relevance=0.8,
                urgency=0.6,
                actionability=0.5,
                novelty=0.6,
                now=moment,
            )
        )

    for signal in conn.execute(
        "SELECT * FROM signals WHERE processed = 0 ORDER BY ts ASC"
    ).fetchall():
        signal_dict = dict(signal)
        payload = json_load(signal_dict["payload"]) or {}
        created.append(
            _candidate(
                conn,
                kind="signal:{}".format(signal_dict["kind"]),
                title=str(payload.get("title") or signal_dict["kind"]),
                body={"signal_id": signal_dict["id"], "payload": payload},
                relevance=0.6,
                urgency=0.5,
                actionability=0.4,
                novelty=0.8,
                now=moment,
            )
        )
        conn.execute("UPDATE signals SET processed = 1 WHERE id = ?", (signal_dict["id"],))
    conn.commit()

    return [item for item in created if item]


def list_candidates(
    conn: sqlite3.Connection, status: str = "new", limit: int = 20, offset: int = 0
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM candidates WHERE status = ? ORDER BY score DESC LIMIT ? OFFSET ?",
        (status, max(int(limit), 0), max(int(offset), 0)),
    ).fetchall()
    items = []
    for row in rows:
        item = row_to_dict(row)
        item["body"] = json_load(item["body"])
        items.append(item)
    return items


def proactive(
    conn: sqlite3.Connection,
    now: Optional[datetime] = None,
    deliver: bool = False,
) -> Dict[str, Any]:
    moment = _now(now)
    generate_candidates(conn, now=moment)
    budget = ensure_budget(conn, moment)
    min_score = float(_setting(conn, "proactivity.min_score"))
    remaining = max(budget["max_sent"] - budget["sent"], 0)
    candidates = list_candidates(conn, status="new", limit=50)
    selected = [item for item in candidates if item["score"] >= min_score][:remaining]

    deferred = False
    if deliver and not can_interrupt(conn, moment):
        selected = []
        deferred = True

    if deliver and not deferred:
        for item in selected:
            conn.execute(
                "UPDATE candidates SET status = 'delivered' WHERE id = ?", (item["id"],)
            )
            record_interrupt(conn, moment)
        if selected:
            briefing_id = uuid4().hex
            conn.execute(
                "INSERT INTO briefings(id, kind, date, payload, delivered_at, created_at) VALUES(?, 'proactive', ?, ?, ?, ?)",
                (
                    briefing_id,
                    moment.date().isoformat(),
                    canonical(selected),
                    utc_now(),
                    utc_now(),
                ),
            )
            conn.commit()
            obs.incr(conn, "proactivity.delivered", len(selected))
        budget = ensure_budget(conn, moment)

    return {
        "budget": budget,
        "candidates": candidates,
        "selected": selected,
        "can_interrupt": can_interrupt(conn, moment),
        "deferred": deferred,
    }


def feedback(conn: sqlite3.Connection, target: str, action: str) -> Dict[str, Any]:
    if action not in ("accept", "ignore", "mute"):
        raise ValueError("feedback action must be accept, ignore, or mute")
    feedback_id = uuid4().hex
    conn.execute(
        "INSERT INTO feedback(id, target, action, ts) VALUES(?, ?, ?, ?)",
        (feedback_id, target, action, utc_now()),
    )
    status_map = {"accept": "accepted", "ignore": "ignored", "mute": "muted"}
    conn.execute(
        "UPDATE candidates SET status = ? WHERE id = ?", (status_map[action], target)
    )
    conn.commit()
    obs.incr(conn, "proactivity.feedback.{}".format(action))
    return {"id": feedback_id, "target": target, "action": action}


def list_feedback(conn: sqlite3.Connection, limit: int = 50) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM feedback ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()]


def _candidate(
    conn: sqlite3.Connection,
    kind: str,
    title: str,
    body: Dict[str, Any],
    relevance: float,
    urgency: float,
    actionability: float,
    novelty: float,
    now: datetime,
) -> Optional[Dict[str, Any]]:
    day = now.date().isoformat()
    existing = conn.execute(
        """
        SELECT id FROM candidates
        WHERE kind = ? AND title = ? AND date(created_at) = ?
          AND status IN ('new', 'delivered', 'accepted')
        LIMIT 1
        """,
        (kind, title, day),
    ).fetchone()
    if existing:
        return None
    score = (
        0.35 * relevance
        + 0.30 * urgency
        + 0.20 * actionability
        + 0.15 * novelty
    )
    candidate = {
        "id": uuid4().hex,
        "kind": kind,
        "title": title,
        "body": body,
        "score": round(score, 3),
        "urgency": urgency,
        "actionability": actionability,
        "novelty": novelty,
        "status": "new",
        "created_at": now.isoformat(),
    }
    conn.execute(
        """
        INSERT INTO candidates(id, signal_id, kind, title, body, score, urgency, actionability, novelty, status, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
        """,
        (
            candidate["id"],
            body.get("signal_id"),
            kind,
            title,
            canonical(body),
            candidate["score"],
            urgency,
            actionability,
            novelty,
            candidate["created_at"],
        ),
    )
    conn.commit()
    return candidate
