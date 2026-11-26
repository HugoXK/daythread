"""Export memory and local state so nothing is locked in."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

from .db import json_load, row_to_dict


def export_json(
    conn: sqlite3.Connection, include_sensitive: bool = False
) -> Dict[str, Any]:
    facts = []
    where = "" if include_sensitive else " WHERE sensitivity NOT IN ('secret', 'health')"
    for row in conn.execute(
        "SELECT * FROM facts{} ORDER BY updated_at DESC".format(where)
    ).fetchall():
        item = row_to_dict(row)
        item["object"] = json_load(item["object"])
        item["provenance"] = json_load(item["provenance"])
        facts.append(item)
    tasks = [row_to_dict(row) for row in conn.execute("SELECT * FROM tasks").fetchall()]
    events = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM calendar_events").fetchall()
    ]
    runs = []
    for row in conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall():
        item = row_to_dict(row)
        item["args"] = json_load(item["args"])
        runs.append(item)
    return {
        "schema_version": "1",
        "facts": facts,
        "tasks": tasks,
        "calendar_events": events,
        "runs": runs,
    }


def export_markdown(
    conn: sqlite3.Connection, include_sensitive: bool = False
) -> str:
    data = export_json(conn, include_sensitive=include_sensitive)
    lines: List[str] = ["# Daythread memory export", ""]

    lines.append("## Facts")
    lines.append("")
    if not data["facts"]:
        lines.append("_No facts._")
    for fact in data["facts"]:
        status = " (deleted)" if fact.get("deleted_at") else ""
        lines.append(
            "- **{}** · {} · {}{}".format(
                fact["subject_id"],
                fact["predicate"],
                _value(fact["object"]),
                status,
            )
        )
        lines.append(
            "  - type: {}, confidence: {:.2f}, provenance: {}".format(
                fact["subject_type"],
                fact["confidence"],
                ", ".join(fact["provenance"]) or "none",
            )
        )

    lines.append("")
    lines.append("## Tasks")
    lines.append("")
    if not data["tasks"]:
        lines.append("_No tasks._")
    for task in data["tasks"]:
        due = " (due {})".format(task["due"]) if task["due"] else ""
        lines.append("- [{}] {}{}".format(task["status"], task["title"], due))

    lines.append("")
    lines.append("## Calendar")
    lines.append("")
    if not data["calendar_events"]:
        lines.append("_No events._")
    for event in data["calendar_events"]:
        lines.append("- {}  {}".format(event["start"], event["title"]))

    lines.append("")
    lines.append("## Runs")
    lines.append("")
    if not data["runs"]:
        lines.append("_No runs._")
    for run in data["runs"]:
        lines.append("- {}  {}  {}  risk={}".format(run["id"], run["state"], run["tool"], run["risk"]))

    return "\n".join(lines) + "\n"


def _value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
