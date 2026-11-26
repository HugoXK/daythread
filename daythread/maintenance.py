"""Retention, pruning, and database housekeeping."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

FINAL_RUN_STATES = (
    "completed",
    "failed",
    "rejected",
    "denied",
    "undone",
    "cancelled",
    "undo_failed",
)


def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    tables = (
        "facts",
        "events",
        "runs",
        "run_steps",
        "audit",
        "model_usage",
        "idempotency",
        "connector_items",
        "messages",
        "candidates",
        "signals",
        "notifications",
        "scheduler_runs",
    )
    counts = {}
    for table in tables:
        counts[table] = conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    return {
        "counts": counts,
        "db_bytes": int(page_count) * int(page_size),
        "free_pages": conn.execute("PRAGMA freelist_count").fetchone()[0],
    }


def prune(
    conn: sqlite3.Connection,
    older_than_days: int = 90,
    keep_recent_runs: int = 200,
    vacuum: bool = False,
) -> Dict[str, int]:
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")
    keep_recent_runs = max(int(keep_recent_runs), 0)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=int(older_than_days))
    ).replace(microsecond=0).isoformat()
    removed: Dict[str, int] = {}

    run_filter = "state IN ({}) AND updated_at < ?".format(
        ", ".join("?" for _ in FINAL_RUN_STATES)
    )
    params = list(FINAL_RUN_STATES) + [cutoff]
    run_ids_sql = (
        "SELECT id FROM runs WHERE {} ORDER BY updated_at DESC LIMIT -1 OFFSET ?".format(
            run_filter
        )
    )
    run_params = params + [keep_recent_runs]
    removed["run_steps"] = conn.execute(
        "DELETE FROM run_steps WHERE run_id IN ({})".format(run_ids_sql),
        run_params,
    ).rowcount
    removed["runs"] = conn.execute(
        "DELETE FROM runs WHERE id IN ({})".format(run_ids_sql),
        run_params,
    ).rowcount

    simple = (
        ("events", "ts"),
        ("audit", "ts"),
        ("model_usage", "ts"),
        ("idempotency", "ts"),
        ("connector_items", "ts"),
        ("messages", "ts"),
        ("candidates", "created_at"),
        ("signals", "ts"),
        ("feedback", "ts"),
        ("briefings", "created_at"),
        ("notifications", "ts"),
        ("scheduler_runs", "started_at"),
        ("model_cache", "expires_at"),
    )
    for table, column in simple:
        removed[table] = conn.execute(
            "DELETE FROM {} WHERE {} < ?".format(table, column), (cutoff,)
        ).rowcount

    old_window = int(time.time()) - 2 * 86400
    removed["api_rate"] = conn.execute(
        "DELETE FROM api_rate WHERE window_start < ?", (old_window,)
    ).rowcount

    conn.execute("PRAGMA optimize")
    conn.commit()
    if vacuum:
        conn.execute("VACUUM")
    return {key: int(value) for key, value in removed.items() if value}
