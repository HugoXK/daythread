"""Industrial run engine: plans, DAGs, retries, checkpoints, resume, undo."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from . import obs, planner, policy, sandbox
from .db import canonical, get_setting, json_load, row_to_dict, utc_now
from .ledger import append_step, get_steps, verify_run
from .tools import ToolContext, ToolResult, apply_undo, get_tool

RISK_ORDER = planner.RISK_ORDER
FINAL_STATES = ("completed", "failed", "rejected", "denied", "undone", "cancelled", "undo_failed")


def _update_state(conn: sqlite3.Connection, run_id: str, state: str, error: Optional[str] = None) -> None:
    if error is None:
        conn.execute(
            "UPDATE runs SET state = ?, updated_at = ? WHERE id = ?",
            (state, utc_now(), run_id),
        )
    else:
        conn.execute(
            "UPDATE runs SET state = ?, error = ?, updated_at = ? WHERE id = ?",
            (state, error, utc_now(), run_id),
        )
    if state in FINAL_STATES:
        conn.execute(
            "UPDATE runs SET lease_owner = NULL, lease_until = NULL WHERE id = ?",
            (run_id,),
        )
    conn.commit()


def _lease_owner() -> str:
    return "{}:{}".format(os.getpid(), uuid4().hex[:8])


def _lease_until(conn: sqlite3.Connection) -> str:
    seconds = int(get_setting(conn, "runtime.lease_seconds", "60") or 60)
    return (datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 5))).replace(
        microsecond=0
    ).isoformat()


def _claim_run(conn: sqlite3.Connection, run_id: str, owner: str) -> bool:
    now = utc_now()
    until = _lease_until(conn)
    cursor = conn.execute(
        """
        UPDATE runs
        SET state = 'running', lease_owner = ?, lease_until = ?, updated_at = ?
        WHERE id = ? AND state IN ('approved', 'running')
          AND (lease_owner IS NULL OR lease_owner = ? OR lease_until IS NULL OR lease_until < ?)
        """,
        (owner, until, now, run_id, owner, now),
    )
    conn.commit()
    return cursor.rowcount == 1


def _heartbeat(conn: sqlite3.Connection, run_id: str, owner: str) -> None:
    cursor = conn.execute(
        "UPDATE runs SET lease_until = ? WHERE id = ? AND lease_owner = ?",
        (_lease_until(conn), run_id, owner),
    )
    conn.commit()
    if cursor.rowcount != 1:
        raise RuntimeError("run lease lost for {}".format(run_id))


def _release_lease(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "UPDATE runs SET lease_owner = NULL, lease_until = NULL WHERE id = ?",
        (run_id,),
    )
    conn.commit()


def _db_file(conn: sqlite3.Connection) -> Optional[str]:
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row["name"] == "main" and row["file"]:
            return row["file"]
    return None


def _kill_process_group(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
    else:  # pragma: no cover - Windows
        proc.kill()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:  # pragma: no cover
        pass


def _run_isolated(
    conn: sqlite3.Connection,
    home: Path,
    run_id: str,
    step: Dict[str, Any],
    timeout: float,
    allow_network: bool = True,
) -> Optional[ToolResult]:
    file_db = _db_file(conn)
    if not file_db:
        return None
    step_key = "{}:{}".format(run_id, step["id"])
    command = [
        sys.executable,
        "-m",
        "daythread.tool_runner",
        "--db",
        file_db,
        "--home",
        str(home),
        "--run-id",
        run_id,
        "--tool",
        step["tool"],
        "--args",
        json.dumps(step["args"], ensure_ascii=False),
        "--idempotency-key",
        step_key,
    ]
    command = sandbox.wrap(command, conn, Path(home), allow_network=allow_network)
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        raise TimeoutError("isolated step timed out after {:.1f}s".format(timeout))
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(
            "isolated tool produced no output: {}".format(stderr[-500:].strip())
        )
    try:
        payload = json.loads(lines[-1])
    except ValueError:
        raise RuntimeError("isolated tool returned invalid JSON: {}".format(lines[-1][:200]))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "isolated tool failed"))
    result = payload.get("result") or {}
    return ToolResult(
        summary=str(result.get("summary", "")),
        undo=result.get("undo"),
        irreversible=bool(result.get("irreversible", False)),
        no_op=bool(result.get("no_op", False)),
        data=result.get("data") or {},
    )


def _run_with_timeout(fn, ctx, args, timeout: float):
    """Enforce a wall-clock timeout when running on the main thread.

    Signal-based timeouts are safe for the CLI. Server requests run in worker
    threads; they fall back to no timeout because a signal alarm is process-wide.
    """
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        return fn(ctx, args)

    def _raise(signum, frame):
        raise TimeoutError("step timed out after {:.1f}s".format(timeout))

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return fn(ctx, args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _update_checkpoint(conn: sqlite3.Connection, run_id: str, checkpoint: Dict[str, Any]) -> None:
    conn.execute(
        "UPDATE runs SET checkpoint = ?, updated_at = ? WHERE id = ?",
        (canonical(checkpoint), utc_now(), run_id),
    )
    conn.commit()


def _default_budget(conn: sqlite3.Connection, override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    budget = {
        "max_steps": int(get_setting(conn, "budget.max_steps", "20") or 20),
        "max_seconds": int(get_setting(conn, "budget.max_seconds", "120") or 120),
        "max_retries": int(get_setting(conn, "budget.max_retries", "1") or 1),
    }
    if override:
        budget.update(override)
    return budget


def _decisions(conn: sqlite3.Connection, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for step in steps:
        spec = get_tool(step["tool"])
        items.append(
            {
                "id": step["id"],
                "tool": step["tool"],
                "risk": spec.risk,
                "decision": policy.decide(conn, spec.risk),
            }
        )
    return items


def _aggregate(decisions: List[Dict[str, Any]]) -> Dict[str, str]:
    if any(item["decision"] == "deny" for item in decisions):
        return {"decision": "deny", "state": "denied", "risk": _max_risk(decisions)}
    if any(item["decision"] == "ask" for item in decisions):
        return {"decision": "ask", "state": "pending_approval", "risk": _max_risk(decisions)}
    return {"decision": "allow", "state": "approved", "risk": _max_risk(decisions)}


def _max_risk(decisions: List[Dict[str, Any]]) -> str:
    risk = "read"
    for item in decisions:
        if RISK_ORDER[item["risk"]] > RISK_ORDER[risk]:
            risk = item["risk"]
    return risk


def propose_plan(
    conn: sqlite3.Connection,
    home: Path,
    steps: List[Dict[str, Any]],
    intent: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    budget: Optional[Dict[str, Any]] = None,
    parent_run: Optional[str] = None,
    force_approval: bool = False,
    taint: str = "trusted",
) -> Dict[str, Any]:
    normalized = planner.validate(steps)
    if taint not in ("trusted", "untrusted"):
        raise ValueError("taint must be trusted or untrusted")

    if idempotency_key:
        existing = conn.execute(
            "SELECT id FROM runs WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing:
            return get_run(conn, existing["id"])

    decisions = _decisions(conn, normalized)
    aggregate = _aggregate(decisions)
    if (force_approval or taint == "untrusted") and aggregate["state"] == "approved":
        if any(item["risk"] != "read" for item in decisions):
            aggregate = {
                "decision": "ask",
                "state": "pending_approval",
                "risk": aggregate["risk"],
            }
    run_budget = _default_budget(conn, budget)
    run_id = uuid4().hex
    now = utc_now()
    trace_id = obs.new_trace_id()
    single = len(normalized) == 1
    tool_name = normalized[0]["tool"] if single else "plan"
    args = normalized[0]["args"] if single else {"steps": normalized}
    plan = {"steps": normalized}
    intent_text = intent or (
        "{} {}".format(tool_name, _compact(args))
        if single
        else "plan with {} steps".format(len(normalized))
    )

    try:
        conn.execute(
            """
            INSERT INTO runs(
                id, intent, tool, args, state, risk, decision, created_at, updated_at,
                plan, budget, checkpoint, trace_id, idempotency_key, parent_run, taint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                intent_text,
                tool_name,
                canonical(args),
                aggregate["state"],
                aggregate["risk"],
                aggregate["decision"],
                now,
                now,
                canonical(plan),
                canonical(run_budget),
                canonical({"completed": []}),
                trace_id,
                idempotency_key,
                parent_run,
                taint,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT id FROM runs WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing:
            return get_run(conn, existing["id"])
        raise

    append_step(
        conn,
        run_id,
        "proposed" if single else "plan",
        {
            "tool": tool_name,
            "args": args,
            "intent": intent,
            "risk": aggregate["risk"],
            "taint": taint,
            "steps": normalized if not single else None,
        },
    )
    append_step(
        conn,
        run_id,
        "policy",
        {"risk": aggregate["risk"], "decision": aggregate["decision"], "decisions": decisions},
    )
    obs.audit(
        conn,
        "run.proposed",
        actor="agent",
        target=run_id,
        detail={"intent": intent_text, "risk": aggregate["risk"], "decision": aggregate["decision"]},
        trace_id=trace_id,
    )
    obs.incr(conn, "runs.created")

    if aggregate["state"] == "approved":
        return execute_run(conn, home, run_id)
    if aggregate["state"] == "denied":
        obs.incr(conn, "runs.denied")
        append_step(conn, run_id, "denied", {"reason": "policy", "decisions": decisions})
    else:
        obs.incr(conn, "runs.pending_approval")
    return get_run(conn, run_id)


def propose(
    conn: sqlite3.Connection,
    home: Path,
    tool_name: str,
    args: Dict[str, Any],
    intent: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    taint: str = "trusted",
) -> Dict[str, Any]:
    return propose_plan(
        conn,
        home,
        [{"id": "s1", "tool": tool_name, "args": args}],
        intent=intent,
        idempotency_key=idempotency_key,
        taint=taint,
    )


def get_run(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError("run not found: {}".format(run_id))
    run = row_to_dict(row)
    run["args"] = json_load(run["args"])
    run["plan"] = json_load(run["plan"]) if run.get("plan") else {
        "steps": [
            {
                "id": "s1",
                "tool": run["tool"],
                "args": run["args"],
                "depends_on": [],
                "max_attempts": 2,
            }
        ]
    }
    run["budget"] = json_load(run.get("budget") or "{}")
    run["checkpoint"] = json_load(run.get("checkpoint") or "{}")
    run["steps"] = get_steps(conn, run_id)
    return run


def list_runs(conn: sqlite3.Connection, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (max(int(limit), 0), max(int(offset), 0)),
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


def approve(
    conn: sqlite3.Connection,
    home: Path,
    run_id: str,
    approver: str = "user",
) -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] != "pending_approval":
        raise ValueError("run {} is {}, not pending_approval".format(run_id, run["state"]))
    append_step(conn, run_id, "approval", {"approver": approver})
    _update_state(conn, run_id, "approved")
    obs.audit(conn, "run.approved", actor=approver, target=run_id, detail={"risk": run["risk"]}, trace_id=run.get("trace_id"))
    obs.incr(conn, "approvals.approved")
    execute_run(conn, home, run_id)
    return get_run(conn, run_id)


def reject(conn: sqlite3.Connection, run_id: str, approver: str = "user") -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] != "pending_approval":
        raise ValueError("run {} is {}, not pending_approval".format(run_id, run["state"]))
    append_step(conn, run_id, "rejection", {"approver": approver})
    _update_state(conn, run_id, "rejected")
    obs.audit(conn, "run.rejected", actor=approver, target=run_id, trace_id=run.get("trace_id"))
    obs.incr(conn, "approvals.rejected")
    return get_run(conn, run_id)


def execute_run(conn: sqlite3.Connection, home: Path, run_id: str) -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] not in ("approved", "running"):
        raise ValueError("run {} is {}, not executable".format(run_id, run["state"]))
    owner = _lease_owner()
    if not _claim_run(conn, run_id, owner):
        raise ValueError("run {} is locked by another executor".format(run_id))
    steps = planner.toposort(run["plan"]["steps"])
    single = len(steps) == 1
    if not single:
        append_step(conn, run_id, "plan.started", {"steps": [step["id"] for step in steps]})

    checkpoint = run["checkpoint"] or {"completed": []}
    completed = set(checkpoint.get("completed", []))
    budget = run["budget"] or _default_budget(conn)

    for step in steps:
        if step["id"] in completed:
            continue
        if not set(step["depends_on"]).issubset(completed):
            return _fail_run(conn, run_id, "dependency not satisfied for {}".format(step["id"]))
        if len(completed) >= int(budget["max_steps"]):
            return _fail_run(conn, run_id, "max_steps budget exceeded")
        if _elapsed_seconds(run["created_at"]) > int(budget["max_seconds"]):
            return _fail_run(conn, run_id, "max_seconds budget exceeded")
        if get_run(conn, run_id)["state"] == "cancelled":
            append_step(conn, run_id, "cancelled", {"reason": "between steps"})
            _release_lease(conn, run_id)
            return get_run(conn, run_id)
        try:
            _execute_step(conn, home, run, step, single, budget)
            _heartbeat(conn, run_id, owner)
        except Exception as exc:  # noqa: BLE001 - record and stop the run
            return _fail_run(conn, run_id, "{}: {}".format(type(exc).__name__, exc))
        completed.add(step["id"])
        checkpoint["completed"] = sorted(completed)
        _update_checkpoint(conn, run_id, checkpoint)

    if _elapsed_seconds(run["created_at"]) > int(budget["max_seconds"]):
        return _fail_run(conn, run_id, "max_seconds budget exceeded", completed=completed)
    _update_state(conn, run_id, "completed")
    _release_lease(conn, run_id)
    if single:
        append_step(conn, run_id, "run.completed", {"step_id": steps[0]["id"]})
    else:
        append_step(conn, run_id, "plan.completed", {"steps": sorted(completed)})
    obs.incr(conn, "runs.completed")
    obs.audit(conn, "run.completed", actor="agent", target=run_id, detail={"steps": sorted(completed)}, trace_id=run.get("trace_id"))
    return get_run(conn, run_id)


def _execute_step(
    conn: sqlite3.Connection,
    home: Path,
    run: Dict[str, Any],
    step: Dict[str, Any],
    single: bool,
    budget: Dict[str, Any],
) -> None:
    spec = get_tool(step["tool"])
    step_key = "{}:{}".format(run["id"], step["id"])
    existing = conn.execute(
        "SELECT * FROM idempotency WHERE key = ?", (step_key,)
    ).fetchone()
    if existing and existing["status"] == "done":
        append_step(
            conn,
            run["id"],
            "executed" if single else "step.succeeded",
            {
                "step_id": step["id"],
                "tool": step["tool"],
                "attempt": 0,
                "summary": "skipped: idempotency hit",
                "undo": None,
                "irreversible": False,
                "data": json_load(existing["result"]) if existing["result"] else {},
                "idempotent_hit": True,
            },
        )
        return
    if existing and not spec.idempotent and existing["status"] in ("started", "failed"):
        raise RuntimeError(
            "step {} has an uncertain external outcome; review before resume".format(step["id"])
        )

    if not spec.idempotent and not existing:
        conn.execute(
            "INSERT INTO idempotency(key, run_id, step_key, status, ts) VALUES(?, ?, ?, 'started', ?)",
            (step_key, run["id"], step["id"], utc_now()),
        )
        conn.commit()

    max_attempts = min(int(step.get("max_attempts", 1)), max(1, int(budget.get("max_retries", 1)) + 1))
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        append_step(
            conn,
            run["id"],
            "execution_started" if single else "step.started",
            {"step_id": step["id"], "tool": step["tool"], "attempt": attempt},
        )
        ctx = ToolContext(conn=conn, home=Path(home), run_id=run["id"], idempotency_key=step_key)
        timeout = float(
            step.get("timeout_seconds")
            or get_setting(conn, "runtime.step_timeout_seconds", "60")
            or 60
        )
        try:
            result = None
            if spec.isolated:
                result = _run_isolated(
                    conn,
                    Path(home),
                    run["id"],
                    step,
                    max(timeout, 0.1),
                    allow_network=spec.network,
                )
            if result is None:
                result = _run_with_timeout(spec.handler, ctx, step["args"], max(timeout, 0.1))
        except Exception as exc:  # noqa: BLE001 - recorded per attempt
            last_error = exc
            append_step(
                conn,
                run["id"],
                "execution_failed" if single else "step.failed",
                {
                    "step_id": step["id"],
                    "tool": step["tool"],
                    "attempt": attempt,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            obs.incr(conn, "steps.failed")
            if attempt < max_attempts and spec.idempotent:
                time.sleep(min(0.05 * attempt, 0.2))
                continue
            if not spec.idempotent:
                conn.execute(
                    "UPDATE idempotency SET status = 'failed', ts = ? WHERE key = ?",
                    (utc_now(), step_key),
                )
                conn.commit()
            raise

        payload = {
            "step_id": step["id"],
            "tool": step["tool"],
            "attempt": attempt,
            "summary": result.summary,
            "undo": result.undo,
            "irreversible": result.irreversible,
            "no_op": bool(result.no_op),
            "data": result.data,
        }
        if spec.idempotent:
            conn.execute(
                "INSERT OR REPLACE INTO idempotency(key, run_id, step_key, status, result, ts) VALUES(?, ?, ?, 'done', ?, ?)",
                (step_key, run["id"], step["id"], canonical(payload), utc_now()),
            )
        else:
            conn.execute(
                "UPDATE idempotency SET status = 'done', result = ?, ts = ? WHERE key = ?",
                (canonical(payload), utc_now(), step_key),
            )
        conn.commit()
        append_step(conn, run["id"], "executed" if single else "step.succeeded", payload)
        obs.incr(conn, "steps.succeeded")
        return
    if last_error:
        raise last_error


def _uncertain_non_idempotent_steps(
    conn: sqlite3.Connection, run: Dict[str, Any]
) -> List[str]:
    """Return non-idempotent steps whose external outcome is not known."""
    uncertain = []
    for step in (run.get("plan") or {}).get("steps", []):
        try:
            spec = get_tool(step["tool"])
        except KeyError:
            continue
        if spec.idempotent:
            continue
        row = conn.execute(
            "SELECT status FROM idempotency WHERE key = ?",
            ("{}:{}".format(run["id"], step["id"]),),
        ).fetchone()
        if row is not None and row["status"] in ("started", "failed"):
            uncertain.append(step["id"])
    return uncertain


def resume_run(conn: sqlite3.Connection, home: Path, run_id: str) -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] not in ("failed", "paused", "running", "approved"):
        raise ValueError("run {} is {}, cannot resume".format(run_id, run["state"]))
    if run["state"] == "failed":
        uncertain = _uncertain_non_idempotent_steps(conn, run)
        if uncertain:
            raise ValueError(
                "run {} has an uncertain external outcome for non-idempotent step(s) {}; "
                "refusing to resume. Verify the external side effect, then inspect "
                "`dt show {}` and `dt ledger {} --verify`; start a new run if a retry "
                "is needed.".format(run_id, ", ".join(uncertain), run_id, run_id)
            )
    append_step(conn, run_id, "resumed", {"from_state": run["state"]})
    _update_state(conn, run_id, "approved")
    obs.incr(conn, "runs.resumed")
    execute_run(conn, home, run_id)
    return get_run(conn, run_id)


def cancel_run(conn: sqlite3.Connection, run_id: str, actor: str = "user") -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] in FINAL_STATES:
        raise ValueError("run {} is already {}".format(run_id, run["state"]))
    append_step(conn, run_id, "cancelled", {"actor": actor})
    _update_state(conn, run_id, "cancelled")
    _release_lease(conn, run_id)
    obs.audit(conn, "run.cancelled", actor=actor, target=run_id, trace_id=run.get("trace_id"))
    obs.incr(conn, "runs.cancelled")
    return get_run(conn, run_id)


def _mark_undone_noop(
    conn: sqlite3.Connection, run_id: str, no_ops: List[Dict[str, Any]]
) -> None:
    """A run whose only writes were no-ops is already at its baseline."""
    conn.execute(
        "UPDATE idempotency SET status = 'undone' WHERE run_id = ?", (run_id,)
    )
    conn.commit()
    _update_state(conn, run_id, "undone")
    append_step(
        conn,
        run_id,
        "undone",
        {"steps": 0, "no_op_steps": [step["id"] for step in no_ops]},
    )
    obs.incr(conn, "runs.undone")
    run = get_run(conn, run_id)
    obs.audit(
        conn,
        "run.undone",
        actor="user",
        target=run_id,
        detail={"steps": 0, "no_op_steps": len(no_ops)},
        trace_id=run.get("trace_id"),
    )


def undo_run(conn: sqlite3.Connection, home: Path, run_id: str) -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if run["state"] != "completed":
        raise ValueError("run {} is {}, only completed runs can be undone".format(run_id, run["state"]))
    verified, errors = verify_run(conn, run_id)
    if not verified:
        raise ValueError(
            "run {} ledger failed verification; refusing to undo before any "
            "external call: {}".format(run_id, "; ".join(errors[:3]) or "unknown error")
        )

    candidates = []
    no_ops = []
    for step in get_steps(conn, run_id):
        if step["kind"] in ("executed", "step.succeeded"):
            payload = step["payload"]
            if payload.get("idempotent_hit"):
                continue
            if payload.get("no_op"):
                no_ops.append(step)
                continue
            if payload.get("irreversible"):
                raise ValueError("run {} contains an irreversible step".format(run_id))
            if not payload.get("undo"):
                raise ValueError("run {} has a step without an undo recipe".format(run_id))
            candidates.append((step, payload["undo"]))

    if not candidates:
        if no_ops:
            _mark_undone_noop(conn, run_id, no_ops)
            return get_run(conn, run_id)
        raise ValueError("run {} has no reversible steps".format(run_id))

    ctx = ToolContext(conn=conn, home=Path(home), run_id=run_id)
    undone = 0
    try:
        for step, recipe in reversed(candidates):
            summary = apply_undo(ctx, recipe)
            append_step(
                conn,
                run_id,
                "undo.step",
                {
                    "undone_step": step["id"] if "step_id" not in step["payload"] else step["payload"]["step_id"],
                    "summary": summary,
                    "idempotency_key": recipe.get("idempotency_key"),
                },
            )
            undone += 1
    except Exception as exc:  # noqa: BLE001 - record partial undo
        append_step(
            conn,
            run_id,
            "undo.failed",
            {"error": str(exc), "error_type": type(exc).__name__},
        )
        _update_state(conn, run_id, "undo_failed", error=str(exc))
        obs.incr(conn, "runs.undo_failed")
        raise

    conn.execute("UPDATE idempotency SET status = 'undone' WHERE run_id = ?", (run_id,))
    conn.commit()
    _update_state(conn, run_id, "undone")
    append_step(conn, run_id, "undone", {"steps": undone})
    obs.incr(conn, "runs.undone")
    obs.audit(conn, "run.undone", actor="user", target=run_id, detail={"steps": undone}, trace_id=run.get("trace_id"))
    return get_run(conn, run_id)


def _fail_run(
    conn: sqlite3.Connection,
    run_id: str,
    error: str,
    completed: Optional[set] = None,
) -> Dict[str, Any]:
    run = get_run(conn, run_id)
    if completed is not None:
        _update_checkpoint(conn, run_id, {"completed": sorted(completed)})
    _update_state(conn, run_id, "failed", error=error)
    _release_lease(conn, run_id)
    append_step(conn, run_id, "run.failed", {"error": error})
    obs.incr(conn, "runs.failed")
    obs.audit(conn, "run.failed", actor="agent", target=run_id, detail={"error": error}, trace_id=run.get("trace_id"))
    return get_run(conn, run_id)


def _elapsed_seconds(created_at: str) -> float:
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(created_at)
        return max(delta.total_seconds(), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _compact(args: Dict[str, Any]) -> str:
    return " ".join("{}={}".format(key, args[key]) for key in sorted(args))
