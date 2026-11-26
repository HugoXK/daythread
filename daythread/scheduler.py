"""Anchored scheduler: morning/evening briefs, notifications, maintenance."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from . import brief, memory, obs, proactivity
from .db import canonical, json_load, row_to_dict, utc_now
from .errors import ValidationError

JOBS = ("morning", "evening", "maintenance")


def tick(
    conn: sqlite3.Connection,
    home: Path,
    job: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    selected_job = job or ("morning" if moment.hour < 12 else "evening")
    if selected_job not in JOBS:
        raise ValidationError("unknown job: {} (expected {})".format(selected_job, ", ".join(JOBS)))
    scheduled_for = "{}:{}".format(moment.date().isoformat(), selected_job)
    existing = conn.execute(
        "SELECT * FROM scheduler_runs WHERE job = ? AND scheduled_for = ?",
        (selected_job, scheduled_for),
    ).fetchone()
    if existing:
        result = row_to_dict(existing)
        result["detail"] = json_load(result["detail"])
        result["already_ran"] = True
        return result

    run_id = uuid4().hex
    started = utc_now()
    conn.execute(
        """
        INSERT INTO scheduler_runs(id, job, scheduled_for, started_at, status, detail)
        VALUES(?, ?, ?, ?, 'running', '{}')
        """,
        (run_id, selected_job, scheduled_for, started),
    )
    conn.commit()

    try:
        if selected_job == "maintenance":
            decayed = memory.decay(conn)
            conn.execute("PRAGMA optimize")
            detail = {"decayed_facts": decayed}
            status = "ok"
        else:
            proactive = proactivity.proactive(conn, now=moment, deliver=True)
            data = (
                brief.morning_brief(conn, now=moment)
                if selected_job == "morning"
                else brief.evening_brief(conn, now=moment)
            )
            title = "Morning brief" if selected_job == "morning" else "Evening brief"
            body = brief.render(data)
            delivered = bool(proactive["selected"]) and not proactive["deferred"]
            notification_id = _notify(
                conn,
                home,
                kind=selected_job,
                title=title,
                body=body,
                delivered=delivered,
                moment=moment,
            )
            status = "delivered" if delivered else "queued"
            detail = {
                "notification_id": notification_id,
                "candidates": len(proactive["candidates"]),
                "selected": len(proactive["selected"]),
                "deferred": proactive["deferred"],
                "budget": proactive["budget"],
            }
            conn.execute(
                """
                INSERT INTO briefings(id, kind, date, payload, delivered_at, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    selected_job,
                    moment.date().isoformat(),
                    canonical(data),
                    utc_now() if delivered else None,
                    utc_now(),
                ),
            )
            conn.commit()

        conn.execute(
            """
            UPDATE scheduler_runs SET finished_at = ?, status = ?, detail = ?
            WHERE id = ?
            """,
            (utc_now(), status, canonical(detail), run_id),
        )
        conn.commit()
        obs.incr(conn, "scheduler.{}".format(selected_job))
        obs.audit(
            conn,
            "scheduler.tick",
            actor="scheduler",
            target=selected_job,
            detail={"scheduled_for": scheduled_for, "status": status},
        )
    except Exception as exc:  # noqa: BLE001 - record the failure and keep the daemon alive
        conn.execute(
            "UPDATE scheduler_runs SET finished_at = ?, status = 'error', detail = ? WHERE id = ?",
            (utc_now(), canonical({"error": str(exc), "type": type(exc).__name__}), run_id),
        )
        conn.commit()
        obs.incr(conn, "scheduler.errors")
        return {
            "id": run_id,
            "job": selected_job,
            "scheduled_for": scheduled_for,
            "status": "error",
            "error": str(exc),
            "already_ran": False,
        }

    row = conn.execute("SELECT * FROM scheduler_runs WHERE id = ?", (run_id,)).fetchone()
    result = row_to_dict(row)
    result["detail"] = json_load(result["detail"])
    result["already_ran"] = False
    return result


def run_due(
    conn: sqlite3.Connection,
    home: Path,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    results = []
    hour = moment.hour
    if 6 <= hour < 12:
        results.append(tick(conn, home, "morning", now=moment))
    elif 18 <= hour < 23:
        results.append(tick(conn, home, "evening", now=moment))
    elif hour == 3:
        results.append(tick(conn, home, "maintenance", now=moment))
    return results


def daemon(
    home: Path,
    db_path: Path,
    interval: int = 60,
    max_ticks: Optional[int] = None,
) -> None:
    from .db import connect, init_db, is_initialized

    ticks = 0
    while True:
        conn = connect(db_path)
        try:
            if not is_initialized(conn):
                init_db(conn)
            run_due(conn, home)
        finally:
            conn.close()
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            return
        time.sleep(max(interval, 1))


def list_notifications(conn: sqlite3.Connection, limit: int = 50) -> List[Dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM notifications ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    ]


def list_scheduler_runs(conn: sqlite3.Connection, limit: int = 20) -> List[Dict[str, Any]]:
    items = []
    for row in conn.execute(
        "SELECT * FROM scheduler_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall():
        item = dict(row)
        item["detail"] = json_load(item["detail"])
        items.append(item)
    return items


def _notify(
    conn: sqlite3.Connection,
    home: Path,
    kind: str,
    title: str,
    body: str,
    delivered: bool,
    moment: datetime,
) -> str:
    notification_id = uuid4().hex
    conn.execute(
        """
        INSERT INTO notifications(id, ts, kind, title, body, status, delivered_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            notification_id,
            moment.isoformat(),
            kind,
            title,
            body,
            "delivered" if delivered else "queued",
            utc_now() if delivered else None,
        ),
    )
    conn.commit()
    if delivered:
        outbox = Path(home) / "outbox" / "notifications.jsonl"
        outbox.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "id": notification_id,
            "ts": moment.isoformat(),
            "kind": kind,
            "title": title,
            "body": body,
        }
        with outbox.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        obs.incr(conn, "notifications.delivered")
    else:
        obs.incr(conn, "notifications.queued")
    return notification_id
