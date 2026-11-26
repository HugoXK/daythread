"""Morning and evening briefs from local state."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import json_load, row_to_dict


def _today(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _row(row: sqlite3.Row) -> Dict[str, Any]:
    return row_to_dict(row)


def _fact(row: sqlite3.Row) -> Dict[str, Any]:
    item = _row(row)
    item["object"] = json_load(item["object"])
    return item


def _run(row: sqlite3.Row) -> Dict[str, Any]:
    item = _row(row)
    item["args"] = json_load(item["args"])
    return item


def _facts(conn: sqlite3.Connection, sql: str, params: tuple) -> List[Dict[str, Any]]:
    return [_fact(row) for row in conn.execute(sql, params).fetchall()]


def morning_brief(
    conn: sqlite3.Connection, now: Optional[datetime] = None
) -> Dict[str, Any]:
    day = _today(now).date().isoformat()
    events = [
        _row(row)
        for row in conn.execute(
            "SELECT * FROM calendar_events WHERE date(start) = ? ORDER BY start",
            (day,),
        ).fetchall()
    ]
    tasks = [
        _row(row)
        for row in conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'open' AND (due IS NULL OR date(due) <= ?)
            ORDER BY CASE WHEN due IS NULL THEN 1 ELSE 0 END, due
            """,
            (day,),
        ).fetchall()
    ]
    commitments = _facts(
        conn,
        """
        SELECT * FROM facts
        WHERE deleted_at IS NULL
          AND (predicate IN ('commitment', 'promise', 'due') OR subject_type = 'goal')
        ORDER BY updated_at DESC LIMIT 5
        """,
        (),
    )
    pending = [
        _run(row)
        for row in conn.execute(
            "SELECT * FROM runs WHERE state = 'pending_approval' ORDER BY created_at"
        ).fetchall()
    ]
    return {
        "kind": "morning",
        "date": day,
        "calendar": events,
        "tasks": tasks,
        "commitments": commitments,
        "pending_approvals": pending,
    }


def evening_brief(
    conn: sqlite3.Connection, now: Optional[datetime] = None
) -> Dict[str, Any]:
    base = _today(now)
    today = base.date().isoformat()
    tomorrow = (base + timedelta(days=1)).date().isoformat()
    events = [
        _row(row)
        for row in conn.execute(
            "SELECT * FROM calendar_events WHERE date(start) = ? ORDER BY start",
            (tomorrow,),
        ).fetchall()
    ]
    open_tasks = [
        _row(row)
        for row in conn.execute(
            "SELECT * FROM tasks WHERE status = 'open' ORDER BY due"
        ).fetchall()
    ]
    done_today = [
        _row(row)
        for row in conn.execute(
            "SELECT * FROM tasks WHERE status = 'done' AND date(updated_at) = ?",
            (today,),
        ).fetchall()
    ]
    pending = [
        _run(row)
        for row in conn.execute(
            "SELECT * FROM runs WHERE state = 'pending_approval' ORDER BY created_at"
        ).fetchall()
    ]
    recent = _facts(
        conn,
        "SELECT * FROM facts WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 5",
        (),
    )
    return {
        "kind": "evening",
        "date": today,
        "tomorrow": tomorrow,
        "calendar": events,
        "tasks": open_tasks,
        "completed_today": done_today,
        "pending_approvals": pending,
        "recent_facts": recent,
    }


def render(brief: Dict[str, Any]) -> str:
    kind = brief.get("kind", "morning")
    title = "Morning brief" if kind == "morning" else "Evening brief"
    lines = ["{} — {}".format(title, brief.get("date", ""))]

    if kind == "morning":
        _section(lines, "Calendar", brief.get("calendar", []), _event_line)
        _section(lines, "Tasks", brief.get("tasks", []), _task_line)
        _section(lines, "Commitments", brief.get("commitments", []), _fact_line)
    else:
        _section(lines, "Tomorrow", brief.get("calendar", []), _event_line)
        _section(lines, "Open tasks", brief.get("tasks", []), _task_line)
        _section(
            lines, "Completed today", brief.get("completed_today", []), _task_line
        )
        _section(lines, "Recently remembered", brief.get("recent_facts", []), _fact_line)

    approvals = brief.get("pending_approvals", [])
    if approvals:
        lines.append("")
        lines.append("Waiting for your approval ({})".format(len(approvals)))
        for run in approvals:
            lines.append(
                "  - {}  {}  →  {}".format(
                    run["id"][:8],
                    run["tool"],
                    _args_hint(run.get("args", {})),
                )
            )
    else:
        lines.append("")
        lines.append("Nothing is waiting for your approval.")

    return "\n".join(lines)


def _section(
    lines: List[str],
    title: str,
    items: List[Dict[str, Any]],
    formatter,
) -> None:
    lines.append("")
    if not items:
        lines.append("{}: nothing.".format(title))
        return
    lines.append("{} ({})".format(title, len(items)))
    for item in items:
        lines.append("  - {}".format(formatter(item)))


def _event_line(event: Dict[str, Any]) -> str:
    return "{}  {}".format(event.get("start", ""), event.get("title", ""))


def _task_line(task: Dict[str, Any]) -> str:
    due = task.get("due")
    suffix = " (due {})".format(due) if due else ""
    return "{}{}".format(task.get("title", ""), suffix)


def _fact_line(fact: Dict[str, Any]) -> str:
    value = fact.get("object")
    if not isinstance(value, str):
        import json

        value = json.dumps(value, ensure_ascii=False)
    return "{} · {} · {}".format(
        fact.get("subject_id", ""), fact.get("predicate", ""), value
    )


def _args_hint(args: Dict[str, Any]) -> str:
    for key in ("to", "title", "path", "name"):
        if key in args:
            return "{}={}".format(key, args[key])
    return ""
