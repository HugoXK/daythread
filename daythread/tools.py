"""Local tools with explicit risk tiers, capabilities, and undo recipes."""

from __future__ import annotations

import hashlib
import json
import shlex
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from .db import canonical, utc_now

UNDO_TABLES = ("tasks", "calendar_events", "messages")
UNDO_FIELDS = {
    "tasks": ("title", "status", "due"),
    "calendar_events": ("title", "start", "end"),
    "messages": ("subject", "snippet", "body"),
}
SHELL_ALLOWLIST = ("echo", "pwd", "ls", "cat", "head", "tail", "wc", "date", "whoami")
UNDO_REQUIRED_RISKS = ("external", "destructive")

# Tools that cannot provide an executable undo recipe today. Every entry needs a
# reason; the registry contract below fails at import time without one. Do not
# add a tool here just to silence the check: mark the tool reversible or narrow
# the external effect instead.
IRREVERSIBLE_TOOLS = {
    "message.send": (
        "simulated outbound message: the send has no provider-side delete API, "
        "so the outbox record cannot be recalled"
    ),
    "connector.call": (
        "generic connector dispatch: only actions that declare a per-action undo "
        "recipe (today google.calendar create_event) are reversible; every other "
        "connector action has no provider-side inverse"
    ),
    "mcp.call": (
        "arbitrary MCP server tool: the protocol exposes no stable target id or "
        "inverse operation, so a call cannot be undone generically"
    ),
    "shell.run": (
        "fixed read-only allowlist (echo, pwd, ls, cat, head, tail, wc, date, "
        "whoami) run with shell=False and relative paths only; it makes no "
        "external call and no durable write, so there is no state to restore. "
        "The mark is a conservative declaration for a destructive-tier tool, "
        "not a claim that it changed anything"
    ),
}


@dataclass
class ToolContext:
    conn: sqlite3.Connection
    home: Path
    run_id: str
    idempotency_key: Optional[str] = None
    db_path: Optional[Path] = None


@dataclass
class ToolResult:
    summary: str
    undo: Optional[Dict[str, Any]] = None
    irreversible: bool = False
    no_op: bool = False
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    risk: str
    description: str
    handler: Callable[[ToolContext, Dict[str, Any]], ToolResult]
    capability: str = "general"
    idempotent: bool = True
    isolated: bool = False
    network: bool = True
    provides_undo: bool = False
    irreversible: bool = False
    irreversible_reason: str = ""


def _det_id(seed: str, length: int = 32) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:length]


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
    seed = ctx.idempotency_key or uuid4().hex
    task_id = str(args.get("id") or _det_id(seed))
    now = utc_now()
    cursor = ctx.conn.execute(
        """
        INSERT OR IGNORE INTO tasks(id, title, status, due, source_run, created_at, updated_at)
        VALUES (?, ?, 'open', ?, ?, ?, ?)
        """,
        (task_id, title, args.get("due"), ctx.run_id, now, now),
    )
    ctx.conn.commit()
    if cursor.rowcount == 0:
        return ToolResult(
            summary="task already exists: {}".format(title),
            undo=None,
            no_op=True,
            data={"task_id": task_id, "duplicate": True},
        )
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


def task_update(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    task_id = str(args.get("id", "")).strip()
    if not task_id:
        raise ValueError("id is required")
    row = ctx.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError("task not found: {}".format(task_id))
    updates: Dict[str, Any] = {}
    for field_name in ("title", "status", "due"):
        if field_name in args:
            updates[field_name] = args[field_name]
    if not updates:
        raise ValueError("no updatable fields supplied")
    previous = {key: row[key] for key in updates}
    sets = ", ".join("{} = ?".format(key) for key in updates)
    params = list(updates.values()) + [utc_now(), task_id]
    ctx.conn.execute(
        "UPDATE tasks SET {}, updated_at = ? WHERE id = ?".format(sets), params
    )
    ctx.conn.commit()
    return ToolResult(
        summary="updated task {}".format(task_id[:8]),
        undo={
            "kind": "row.update",
            "table": "tasks",
            "id": task_id,
            "fields": previous,
        },
        data={"task_id": task_id, "fields": sorted(updates)},
    )


def task_list(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    status = args.get("status")
    if status:
        rows = ctx.conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY due", (status,)
        ).fetchall()
    else:
        rows = ctx.conn.execute(
            "SELECT * FROM tasks ORDER BY status, due"
        ).fetchall()
    tasks = [dict(row) for row in rows]
    return ToolResult(
        summary="{} task(s)".format(len(tasks)), data={"tasks": tasks}
    )


def calendar_add(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    title = str(args.get("title", "")).strip()
    start = str(args.get("start", "")).strip()
    if not title or not start:
        raise ValueError("title and start are required")
    seed = ctx.idempotency_key or uuid4().hex
    event_id = str(args.get("id") or _det_id(seed))
    cursor = ctx.conn.execute(
        """
        INSERT OR IGNORE INTO calendar_events(id, title, start, end, source_run, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_id, title, start, args.get("end"), ctx.run_id, utc_now()),
    )
    ctx.conn.commit()
    if cursor.rowcount == 0:
        return ToolResult(
            summary="calendar event already exists: {}".format(title),
            undo=None,
            no_op=True,
            data={"event_id": event_id, "duplicate": True},
        )
    return ToolResult(
        summary="added calendar event: {} at {}".format(title, start),
        undo={"kind": "row.delete", "table": "calendar_events", "id": event_id},
        data={"event_id": event_id},
    )


def calendar_move(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    event_id = str(args.get("id", "")).strip()
    start = str(args.get("start", "")).strip()
    if not event_id or not start:
        raise ValueError("id and start are required")
    row = ctx.conn.execute(
        "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        raise KeyError("calendar event not found: {}".format(event_id))
    previous = {"start": row["start"], "end": row["end"]}
    ctx.conn.execute(
        "UPDATE calendar_events SET start = ?, end = ? WHERE id = ?",
        (start, args.get("end", row["end"]), event_id),
    )
    ctx.conn.commit()
    return ToolResult(
        summary="moved event {} to {}".format(event_id[:8], start),
        undo={
            "kind": "row.update",
            "table": "calendar_events",
            "id": event_id,
            "fields": previous,
        },
        data={"event_id": event_id, "start": start},
    )


def calendar_list(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    date_from = args.get("from")
    date_to = args.get("to")
    clauses = []
    params: List[Any] = []
    if date_from:
        clauses.append("date(start) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date(start) <= ?")
        params.append(date_to)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = ctx.conn.execute(
        "SELECT * FROM calendar_events{} ORDER BY start".format(where), params
    ).fetchall()
    events = [dict(row) for row in rows]
    return ToolResult(
        summary="{} calendar event(s)".format(len(events)), data={"events": events}
    )


def message_draft(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    to = str(args.get("to", "")).strip()
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    if not to:
        raise ValueError("to is required")
    seed = ctx.idempotency_key or uuid4().hex
    draft_id = _det_id(seed, 8)
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
        "idempotency_key": ctx.idempotency_key,
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


def _find_existing_fact(ctx: ToolContext, subject_type, subject_id, predicate, value):
    from . import memory

    row = ctx.conn.execute(
        """
        SELECT id FROM facts
        WHERE deleted_at IS NULL AND superseded_by IS NULL
          AND subject_type = ? AND subject_id = ? AND predicate = ? AND object = ?
        ORDER BY updated_at DESC LIMIT 1
        """,
        (subject_type, subject_id, predicate, canonical(value)),
    ).fetchone()
    return memory.get_fact(ctx.conn, row["id"]) if row else None


def memory_remember(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    from . import memory

    text = str(args.get("text", "")).strip()
    created = []
    existing = []
    if args.get("value") is not None and args.get("predicate"):
        subject_type = str(args.get("subject_type", "self"))
        subject_id = str(args.get("subject_id", "self"))
        predicate = str(args["predicate"])
        found = _find_existing_fact(ctx, subject_type, subject_id, predicate, args.get("value"))
        if found:
            existing.append(found)
        else:
            created.append(
                memory.add_fact(
                    ctx.conn,
                    subject_type,
                    subject_id,
                    predicate,
                    args.get("value"),
                    confidence=float(args.get("confidence", 0.8)),
                    source="tool",
                )
            )
    elif text:
        candidates = memory.extract_facts(text)
        if not candidates:
            raise ValueError("nothing structured found in text")
        for candidate in candidates:
            found = _find_existing_fact(
                ctx,
                candidate["subject_type"],
                candidate["subject_id"],
                candidate["predicate"],
                candidate["value"],
            )
            if found:
                existing.append(found)
            else:
                created.extend(memory.apply_extracted(ctx.conn, [candidate], created_by="tool"))
    else:
        raise ValueError("text or predicate/value is required")
    return ToolResult(
        summary="remembered {} new fact(s), {} duplicate(s)".format(len(created), len(existing)),
        undo={"kind": "facts.delete", "ids": [fact["id"] for fact in created]} if created else None,
        no_op=not created,
        data={"created": created, "existing": existing},
    )


def memory_search(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    from . import memory

    query = str(args.get("q", "")).strip()
    facts = memory.search_facts(ctx.conn, query)
    return ToolResult(
        summary="{} matching fact(s)".format(len(facts)), data={"facts": facts}
    )


def brief_generate(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    from . import brief

    data = brief.evening_brief(ctx.conn) if args.get("evening") else brief.morning_brief(ctx.conn)
    return ToolResult(
        summary="generated {} brief".format(data["kind"]), data={"brief": data}
    )


def connector_sync(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    from . import connectors

    connector_id = str(args.get("connector", "")).strip()
    if not connector_id:
        raise ValueError("connector is required")
    result = connectors.sync(ctx.conn, connector_id, home=ctx.home)
    return ToolResult(
        summary="synced {} ({} new item(s))".format(connector_id, result["inserted"]),
        data=result,
    )


def connector_call(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    from . import connectors

    connector_id = str(args.get("connector", "")).strip()
    action = str(args.get("action", "")).strip()
    arguments = args.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            raise ValueError("arguments must be a JSON object")
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    result = connectors.call_action(
        ctx.conn, ctx.home, connector_id, action, arguments
    )
    undo = result.get("undo")
    if undo:
        undo = dict(undo)
        undo["idempotency_key"] = ctx.idempotency_key or "{}:{}".format(
            ctx.run_id, action
        )
    return ToolResult(
        summary="called connector {}.{}".format(connector_id, action),
        undo=undo,
        irreversible=bool(result.get("irreversible", False)),
        data=result,
    )


def mcp_call(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    from . import mcp

    server = str(args.get("server", "")).strip()
    tool = str(args.get("tool", "")).strip()
    if not server or not tool:
        raise ValueError("server and tool are required")
    arguments = args.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            raise ValueError("arguments must be a JSON object")
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    result = mcp.call(ctx.conn, server, tool, arguments, cwd=ctx.home)
    return ToolResult(
        summary="called MCP {}.{}".format(server, tool),
        data={"server": server, "tool": tool, "result": result},
    )


def shell_run(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    command = str(args.get("command", "")).strip()
    if not command:
        raise ValueError("command is required")
    parts = shlex.split(command)
    if not parts or parts[0] not in SHELL_ALLOWLIST:
        raise ValueError(
            "command not allowed; allowlist: {}".format(", ".join(SHELL_ALLOWLIST))
        )
    for part in parts[1:]:
        if part.startswith("/") or ".." in Path(part).parts:
            raise ValueError("absolute paths and '..' are not allowed in sandbox commands")
    sandbox = _safe_path(ctx.home, "sandbox")
    sandbox.mkdir(parents=True, exist_ok=True)
    timeout = max(1, min(int(args.get("timeout", 5)), 10))
    completed = subprocess.run(
        parts,
        cwd=str(sandbox),
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(sandbox)},
    )
    stdout = completed.stdout[-65536:]
    stderr = completed.stderr[-65536:]
    return ToolResult(
        summary="ran `{}` (exit {})".format(parts[0], completed.returncode),
        irreversible=True,
        data={
            "command": parts,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        },
    )


def _check_undo_contract(registry: Optional[Dict[str, ToolSpec]] = None) -> None:
    """Fail closed when a risky tool does not declare an undo contract.

    This runs at import time so every entry point (CLI, API, MCP) refuses to
    start with a registry that cannot explain how an external effect is
    reversed. Lazy validation in ``get_tool`` would let a run start before the
    registry is known to be safe.
    """
    tools = TOOLS if registry is None else registry
    for spec in tools.values():
        reason = spec.irreversible_reason.strip()
        if spec.provides_undo and spec.irreversible:
            raise ValueError(
                "tool {} (risk {}) declares both provides_undo and irreversible; "
                "pick exactly one".format(spec.name, spec.risk)
            )
        if spec.irreversible:
            if spec.risk not in UNDO_REQUIRED_RISKS:
                raise ValueError(
                    "tool {} (risk {}) may only be irreversible when risk is "
                    "external or destructive".format(spec.name, spec.risk)
                )
            if not reason:
                raise ValueError(
                    "tool {} (risk {}) is irreversible without irreversible_reason".format(
                        spec.name, spec.risk
                    )
                )
            if not spec.capability:
                raise ValueError(
                    "tool {} (risk {}) is irreversible without a capability name".format(
                        spec.name, spec.risk
                    )
                )
        elif reason:
            raise ValueError(
                "tool {} (risk {}) has irreversible_reason but irreversible=False; "
                "set irreversible=True so the declaration is explicit".format(
                    spec.name, spec.risk
                )
            )
        if (
            spec.risk in UNDO_REQUIRED_RISKS
            and not spec.provides_undo
            and not spec.irreversible
        ):
            raise ValueError(
                "tool {} (risk {}) needs an undo contract: set provides_undo=True "
                "or mark it irreversible with irreversible_reason".format(
                    spec.name, spec.risk
                )
            )


TOOLS: Dict[str, ToolSpec] = {
    "note.write": ToolSpec("note.write", "write", "Write a UTF-8 note under notes/.", note_write, "notes.write", provides_undo=True),
    "task.create": ToolSpec("task.create", "write", "Create a local task.", task_create, "tasks.write", provides_undo=True),
    "task.done": ToolSpec("task.done", "write", "Mark a local task as done.", task_done, "tasks.write", provides_undo=True),
    "task.update": ToolSpec("task.update", "write", "Update a local task.", task_update, "tasks.write", provides_undo=True),
    "task.list": ToolSpec("task.list", "read", "List local tasks.", task_list, "tasks.read"),
    "calendar.add": ToolSpec("calendar.add", "write", "Add a local calendar event.", calendar_add, "calendar.write", provides_undo=True),
    "calendar.move": ToolSpec("calendar.move", "write", "Move a local calendar event.", calendar_move, "calendar.write", provides_undo=True),
    "calendar.list": ToolSpec("calendar.list", "read", "List local calendar events.", calendar_list, "calendar.read"),
    "message.draft": ToolSpec("message.draft", "write", "Write a message draft under outbox/drafts/.", message_draft, "messages.draft", provides_undo=True),
    "message.send": ToolSpec(
        "message.send",
        "external",
        "Send a message (simulated, irreversible).",
        message_send,
        "messages.send",
        idempotent=False,
        isolated=True,
        irreversible=True,
        irreversible_reason=IRREVERSIBLE_TOOLS["message.send"],
    ),
    "memory.remember": ToolSpec("memory.remember", "write", "Remember a structured fact or extract from text.", memory_remember, "memory.write", provides_undo=True),
    "memory.search": ToolSpec("memory.search", "read", "Search memory facts.", memory_search, "memory.read"),
    "brief.generate": ToolSpec("brief.generate", "read", "Generate a morning or evening brief.", brief_generate, "brief.read"),
    "connector.sync": ToolSpec("connector.sync", "write", "Sync a connected connector into local state.", connector_sync, "connectors.sync", isolated=True),
    "connector.call": ToolSpec(
        "connector.call",
        "external",
        "Call a write action on a connected connector.",
        connector_call,
        "connectors.action",
        idempotent=False,
        isolated=True,
        irreversible=True,
        irreversible_reason=IRREVERSIBLE_TOOLS["connector.call"],
    ),
    "mcp.call": ToolSpec(
        "mcp.call",
        "external",
        "Call a configured MCP server tool.",
        mcp_call,
        "mcp.call",
        idempotent=False,
        isolated=True,
        irreversible=True,
        irreversible_reason=IRREVERSIBLE_TOOLS["mcp.call"],
    ),
    "shell.run": ToolSpec(
        "shell.run",
        "destructive",
        "Run an allowlisted command in the local sandbox.",
        shell_run,
        "shell.exec",
        idempotent=False,
        isolated=True,
        network=False,
        irreversible=True,
        irreversible_reason=IRREVERSIBLE_TOOLS["shell.run"],
    ),
}

_check_undo_contract()


def get_tool(name: str) -> ToolSpec:
    try:
        return TOOLS[name]
    except KeyError:
        raise KeyError(
            "unknown tool: {} (available: {})".format(name, ", ".join(sorted(TOOLS)))
        )


def list_tools() -> List[ToolSpec]:
    return [TOOLS[name] for name in sorted(TOOLS)]


def _undo_already_applied(ctx: ToolContext, idempotency_key: str) -> bool:
    from .ledger import get_steps

    for step in get_steps(ctx.conn, ctx.run_id):
        if (
            step["kind"] == "undo.step"
            and step["payload"].get("idempotency_key") == idempotency_key
        ):
            return True
    return False


def _apply_connector_undo(ctx: ToolContext, undo: Dict[str, Any]) -> str:
    from . import connectors
    from .ledger import verify_run

    ok, errors = verify_run(ctx.conn, ctx.run_id)
    if not ok:
        raise ValueError(
            "refusing connector undo: run {} ledger failed verification: {}".format(
                ctx.run_id, "; ".join(errors[:3]) or "unknown error"
            )
        )
    kind = str(undo.get("kind", ""))
    if kind != "connector.delete":
        raise ValueError("unknown connector undo recipe: {}".format(kind))
    connector_id = str(undo.get("connector", "")).strip()
    action = str(undo.get("action", "")).strip()
    if not connector_id or not action:
        raise ValueError("connector undo recipe needs connector and action")
    target = undo.get("target")
    if not isinstance(target, dict) or len(target) != 1:
        raise ValueError("connector undo target must be exactly one id")
    target_field, target_id = next(iter(target.items()))
    if not str(target_field).strip() or target_id is None or not str(target_id).strip():
        raise ValueError("connector undo target must be exactly one non-empty id")
    idempotency_key = str(undo.get("idempotency_key") or "").strip()
    if idempotency_key and _undo_already_applied(ctx, idempotency_key):
        return "already undone {} (idempotency key {})".format(
            target_field, idempotency_key
        )
    connectors.undo_action(
        ctx.conn, ctx.home, connector_id, action, {str(target_field): str(target_id)}
    )
    return "undid {}.{} on {}={}".format(
        connector_id, action, target_field, target_id
    )


def apply_undo(ctx: ToolContext, undo: Dict[str, Any]) -> str:
    kind = undo.get("kind")
    if isinstance(kind, str) and kind.startswith("connector."):
        return _apply_connector_undo(ctx, undo)
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
    if kind in ("row.delete", "facts.delete"):
        if kind == "facts.delete":
            ids = undo.get("ids", [])
            for fact_id in ids:
                ctx.conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            ctx.conn.commit()
            return "deleted {} facts".format(len(ids))
        table = str(undo.get("table", ""))
        if table not in UNDO_TABLES:
            raise ValueError("refusing to undo delete on table: {}".format(table))
        row_id = str(undo.get("id", ""))
        ctx.conn.execute("DELETE FROM {} WHERE id = ?".format(table), (row_id,))
        ctx.conn.commit()
        return "deleted {} row {}".format(table, row_id)
    if kind == "row.update":
        table = str(undo.get("table", ""))
        if table not in UNDO_TABLES:
            raise ValueError("refusing to undo update on table: {}".format(table))
        row_id = str(undo.get("id", ""))
        fields = undo.get("fields")
        if fields is None:
            field_name = str(undo.get("field", ""))
            if field_name not in UNDO_FIELDS.get(table, ()):
                raise ValueError("refusing to undo update on {}.".format(field_name))
            fields = {field_name: undo.get("previous")}
        for field_name in fields:
            if field_name not in UNDO_FIELDS.get(table, ()):
                raise ValueError("refusing to undo field {} on {}".format(field_name, table))
        sets = ", ".join("{} = ?".format(key) for key in fields)
        params = list(fields.values()) + [row_id]
        ctx.conn.execute(
            "UPDATE {} SET {} WHERE id = ?".format(table, sets), params
        )
        ctx.conn.commit()
        return "restored {} row {} fields {}".format(table, row_id, ", ".join(fields))
    raise ValueError("unknown undo recipe: {}".format(kind))
