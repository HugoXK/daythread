"""Run state machine: propose, approve, execute, undo."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from . import policy
from .db import json_load, row_to_dict, utc_now
from .ledger import append_step, get_steps, last_executed_step
from .tools import ToolContext, apply_undo, get_tool

DECISION_TO_STATE = {
    "allow": "approved",
    "ask": "pending_approval",
    "deny": "denied",
}


def _update_state(conn: sqlite3.Connection, run_id: str, state: str) -> None:
    conn.execute(
        "UPDATE runs SET state = ?, updated_at = ? WHERE id = ?",
        (state, utc_now(), run_id),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError("run not found: {}".format(run_id))
    run = row_to_dict(row)
    run["args"] = json_load(run["args"])
    run["steps"] = get_steps(conn, run_id)
    return run


def list_runs(conn: sqlite3.Connection, limit: int = 20) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    runs = []
    for row in rows:
        run = row_to_dict(row)
        run["args"] = json_load(run["args"])
        runs.append(run)
    return runs


def pending_runs(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM runs WHERE state = 'pending_approval' ORDER BY created_at ASC"
    ).fetchall()
    runs = []
    for row in rows:
        run = row_to_dict(row)
        run["args"] = json_load(run["args"])
        runs.append(run)
    return runs


def propose(
    conn: sqlite3.Connection,
    home: Path,
    tool_name: str,
    args: Dict[str, Any],
    intent: Optional[str] = None,
) -> Dict[str, Any]:
    spec = get_tool(tool_name)
    decision = policy.decide(conn, spec.risk)
    state = DECISION_TO_STATE[decision]
    run_id = uuid4().hex
    now = utc_now()
    conn.execute(
        """
        INSERT INTO runs(id, intent, tool, args, state, risk, decision, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            intent or "{} {}".format(tool_name, _compact(args)),
            tool_name,
            _encode(args),
            state,
            spec.risk,
            decision,
            now,
            now,
        ),
    )
    conn.commit()
    append_step(
        conn,
        run_id,
        "proposed",
        {"tool": tool_name, "args": args, "intent": intent, "risk": spec.risk},
    )
    append_step(conn, run_id, "policy", {"risk": spec.risk, "decision": decision})

    if decision == "allow":
        _execute(conn, home, run_id)
    elif decision == "deny":
        append_step(conn, run_id, "denied", {"reason": "policy"})

    return get_run(conn, run_id)


def approve(
    conn: sqlite3.Connection,
    home: Path,
    run_id: str,
    approver: str = "user",
) -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] != "pending_approval":
        raise ValueError(
            "run {} is {}, not pending_approval".format(run_id, run["state"])
        )
    append_step(conn, run_id, "approval", {"approver": approver})
    _update_state(conn, run_id, "approved")
    _execute(conn, home, run_id)
    return get_run(conn, run_id)


def reject(conn: sqlite3.Connection, run_id: str, approver: str = "user") -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] != "pending_approval":
        raise ValueError(
            "run {} is {}, not pending_approval".format(run_id, run["state"])
        )
    append_step(conn, run_id, "rejection", {"approver": approver})
    _update_state(conn, run_id, "rejected")
    return get_run(conn, run_id)


def _execute(conn: sqlite3.Connection, home: Path, run_id: str) -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] not in ("approved", "running"):
        raise ValueError("run {} is not approved".format(run_id))
    _update_state(conn, run_id, "running")
    append_step(conn, run_id, "execution_started", {"tool": run["tool"]})
    ctx = ToolContext(conn=conn, home=Path(home), run_id=run_id)
    try:
        spec = get_tool(run["tool"])
        result = spec.handler(ctx, run["args"])
    except Exception as exc:  # noqa: BLE001 - record any tool failure in the ledger
        append_step(
            conn,
            run_id,
            "execution_failed",
            {"error": str(exc), "error_type": type(exc).__name__},
        )
        _update_state(conn, run_id, "failed")
        return get_run(conn, run_id)
    append_step(
        conn,
        run_id,
        "executed",
        {
            "summary": result.summary,
            "undo": result.undo,
            "irreversible": result.irreversible,
            "data": result.data,
        },
    )
    _update_state(conn, run_id, "completed")
    return get_run(conn, run_id)


def undo_run(conn: sqlite3.Connection, home: Path, run_id: str) -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] != "completed":
        raise ValueError(
            "run {} is {}, only completed runs can be undone".format(run_id, run["state"])
        )
    step = last_executed_step(conn, run_id)
    payload = step.get("payload", {})
    if payload.get("irreversible"):
        raise ValueError("run {} is marked irreversible".format(run_id))
    recipe = payload.get("undo")
    if not recipe:
        raise ValueError("run {} has no undo recipe".format(run_id))

    ctx = ToolContext(conn=conn, home=Path(home), run_id=run_id)
    summary = apply_undo(ctx, recipe)
    append_step(
        conn,
        run_id,
        "undone",
        {"summary": summary, "undone_step": step.get("id")},
    )
    _update_state(conn, run_id, "undone")
    return get_run(conn, run_id)


def _encode(args: Dict[str, Any]) -> str:
    import json

    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def _compact(args: Dict[str, Any]) -> str:
    parts = []
    for key in sorted(args):
        parts.append("{}={}".format(key, args[key]))
    return " ".join(parts)
