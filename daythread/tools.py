"""Local tools with explicit risk tiers and undo recipes."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from .db import utc_now

UNDO_TABLES = ("tasks", "calendar_events")


@dataclass
class ToolContext:
    conn: sqlite3.Connection
    home: Path
    run_id: str


@dataclass
class ToolResult:
    summary: str
    undo: Optional[Dict[str, Any]] = None
    irreversible: bool = False
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    risk: str
    description: str
    handler: Callable[[ToolContext, Dict[str, Any]], ToolResult]


def _safe_path(home: Path, relative: str) -> Path:
    base = home.resolve()
    path = (base / relative).resolve()
    if path != base and base not in path.parents:
        raise ValueError("path escapes daythread home: {}".format(relative))
    return path


def _norm_note_path(raw: str) -> str:
    if not raw:
        raise ValueError("path is required")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("path must be relative and stay inside notes/")
    text = candidate.as_posix()
    if not text.startswith("notes/"):
        text = "notes/" + text
    return text


def note_write(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    rel = _norm_note_path(str(args.get("path") or args.get("name") or ""))
    content = str(args.get("content", ""))
    path = _safe_path(ctx.home, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(content, encoding="utf-8")
    return ToolResult(
        summary="wrote {}".format(rel),
        undo={"kind": "file.write", "path": rel, "previous": previous},
        data={"path": rel, "bytes": len(content.encode("utf-8"))},
    )


def task_create(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    title = str(args.get("title", "")).strip()
    if not title:
        raise ValueError("title is required")
    task_id = uuid4().hex
    now = utc_now()
    ctx.conn.execute(
        """
        INSERT INTO tasks(id, title, status, due, source_run, created_at, updated_at)
        VALUES (?, ?, 'open', ?, ?, ?, ?)
        """,
        (task_id, title, args.get("due"), ctx.run_id, now, now),
    )
    ctx.conn.commit()
    return ToolResult(
        summary="created task: {}".format(title),
        undo={"kind": "row.delete", "table": "tasks", "id": task_id},
        data={"task_id": task_id},
    )


def task_done(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    task_id = str(args.get("id", "")).strip()
    if not task_id:
        raise ValueError("id is required")
    row = ctx.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError("task not found: {}".format(task_id))
    previous = row["status"]
    ctx.conn.execute(
        "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
        (utc_now(), task_id),
    )
    ctx.conn.commit()
    return ToolResult(
        summary="completed task: {}".format(row["title"]),
        undo={
            "kind": "row.update",
            "table": "tasks",
            "id": task_id,
            "field": "status",
            "previous": previous,
        },
        data={"task_id": task_id},
    )


def calendar_add(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    title = str(args.get("title", "")).strip()
    start = str(args.get("start", "")).strip()
    if not title or not start:
        raise ValueError("title and start are required")
    event_id = uuid4().hex
    ctx.conn.execute(
        """
        INSERT INTO calendar_events(id, title, start, end, source_run, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_id, title, start, args.get("end"), ctx.run_id, utc_now()),
    )
    ctx.conn.commit()
    return ToolResult(
        summary="added calendar event: {} at {}".format(title, start),
        undo={"kind": "row.delete", "table": "calendar_events", "id": event_id},
        data={"event_id": event_id},
    )


def message_draft(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    to = str(args.get("to", "")).strip()
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    if not to:
        raise ValueError("to is required")
    draft_id = uuid4().hex[:8]
    rel = "outbox/drafts/{}.md".format(draft_id)
    path = _safe_path(ctx.home, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "# Draft\n\nTo: {}\nSubject: {}\n\n{}\n".format(to, subject, body)
    path.write_text(text, encoding="utf-8")
    return ToolResult(
        summary="drafted message to {} ({})".format(to, rel),
        undo={"kind": "file.write", "path": rel, "previous": None},
        data={"draft_id": draft_id, "path": rel},
    )


def message_send(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    to = str(args.get("to", "")).strip()
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    if not to:
        raise ValueError("to is required")
    outbox = _safe_path(ctx.home, "outbox/sent.jsonl")
    outbox.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": utc_now(),
        "run_id": ctx.run_id,
        "to": to,
        "subject": subject,
        "body": body,
    }
    with outbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return ToolResult(
        summary="sent message to {} (simulated)".format(to),
        undo=None,
        irreversible=True,
        data={"to": to, "subject": subject},
    )


TOOLS: Dict[str, ToolSpec] = {
    "note.write": ToolSpec(
        name="note.write",
        risk="write",
        description="Write a UTF-8 note under notes/.",
        handler=note_write,
    ),
    "task.create": ToolSpec(
        name="task.create",
        risk="write",
        description="Create a local task.",
        handler=task_create,
    ),
    "task.done": ToolSpec(
        name="task.done",
        risk="write",
        description="Mark a local task as done.",
        handler=task_done,
    ),
    "calendar.add": ToolSpec(
        name="calendar.add",
        risk="write",
        description="Add a local calendar event.",
        handler=calendar_add,
    ),
    "message.draft": ToolSpec(
        name="message.draft",
        risk="write",
        description="Write a message draft under outbox/drafts/.",
        handler=message_draft,
    ),
    "message.send": ToolSpec(
        name="message.send",
        risk="external",
        description="Send a message (simulated, irreversible).",
        handler=message_send,
    ),
}


def get_tool(name: str) -> ToolSpec:
    try:
        return TOOLS[name]
    except KeyError:
        raise KeyError(
            "unknown tool: {} (available: {})".format(name, ", ".join(sorted(TOOLS)))
        )


def list_tools() -> List[ToolSpec]:
    return [TOOLS[name] for name in sorted(TOOLS)]


def apply_undo(ctx: ToolContext, undo: Dict[str, Any]) -> str:
    kind = undo.get("kind")
    if kind == "file.write":
        rel = str(undo.get("path", ""))
        path = _safe_path(ctx.home, rel)
        previous = undo.get("previous")
        if previous is None:
            if path.exists():
                path.unlink()
            return "removed {}".format(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(previous), encoding="utf-8")
        return "restored {}".format(rel)
    if kind == "row.delete":
        table = str(undo.get("table", ""))
        if table not in UNDO_TABLES:
            raise ValueError("refusing to undo delete on table: {}".format(table))
        row_id = str(undo.get("id", ""))
        ctx.conn.execute("DELETE FROM {} WHERE id = ?".format(table), (row_id,))
        ctx.conn.commit()
        return "deleted {} row {}".format(table, row_id)
    if kind == "row.update":
        table = str(undo.get("table", ""))
        field_name = str(undo.get("field", ""))
        if table not in UNDO_TABLES or field_name not in ("status", "title", "start", "end", "due"):
            raise ValueError("refusing to undo update on {}.".format(field_name))
        row_id = str(undo.get("id", ""))
        ctx.conn.execute(
            "UPDATE {} SET {} = ? WHERE id = ?".format(table, field_name),
            (undo.get("previous"), row_id),
        )
        ctx.conn.commit()
        return "restored {} row {} field {}".format(table, row_id, field_name)
    raise ValueError("unknown undo recipe: {}".format(kind))
