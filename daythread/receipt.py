"""Read-only, self-contained run receipts.

A receipt is a *projection* of the hash-chained run ledger: it never writes
``events`` or ``run_steps``, adds no table and runs no migration. The ledger
stays the only source of truth; a receipt only proves "this chain looked like
this at export time" and can be diffed, archived, or handed to another tool.

Only finished runs produce receipts. A receipt for a run that is still
``pending_approval`` / ``running`` would be a half-truth, so ``build_receipt``
refuses it instead of emitting a partial document.

Privacy rules:

* step payloads are committed to as ``payload_digest`` only, never inlined;
* undo recipes pass through :func:`daythread.obs.redact`, so values under
  sensitive keys (token, secret, password, ...) become ``[REDACTED]``;
* ``facts.delete`` recipes drop ids of facts with ``sensitivity`` ``secret`` or
  ``health``; a recipe left with no ids is dropped entirely;
* everything that remains is still derived from the run, so treat a receipt as
  sensitive as the run it describes.

``version`` is part of the external contract: bump it whenever the meaning of
an existing field changes, and document the old/new semantics in the PR.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Dict, List, Optional

from .db import canonical, json_load
from .ledger import get_steps
from .obs import redact
from .runtime import FINAL_STATES

VERSION = 1

_EXECUTED_KINDS = ("executed", "step.succeeded")
_SENSITIVE_FACT_LEVELS = ("secret", "health")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_run_row(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError("run not found: {}".format(run_id))
    return dict(row)


def _plan_id(run: Dict[str, Any]) -> Any:
    """No plan id is persisted today; the field stays in the contract, null."""
    plan = json_load(run.get("plan") or "{}")
    if isinstance(plan, dict):
        return plan.get("plan_id")
    return None


def _finished_at(run: Dict[str, Any]) -> Any:
    """``updated_at`` only once the run reached a final state, else null."""
    if run.get("state") in FINAL_STATES:
        return run.get("updated_at")
    return None


def _sensitive_fact_ids(conn: sqlite3.Connection, ids: List[Any]) -> set:
    wanted = [str(item) for item in ids]
    if not wanted:
        return set()
    placeholders = ",".join("?" for _ in wanted)
    rows = conn.execute(
        "SELECT id FROM facts WHERE id IN ({}) AND sensitivity IN ({})".format(
            placeholders, ",".join("?" for _ in _SENSITIVE_FACT_LEVELS)
        ),
        wanted + list(_SENSITIVE_FACT_LEVELS),
    ).fetchall()
    return {row["id"] for row in rows}


def _filter_recipe(conn: sqlite3.Connection, recipe: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Redact sensitive keys, then drop sensitive fact ids from ``ids`` lists."""
    cleaned = redact(recipe)
    ids = cleaned.get("ids") if isinstance(cleaned, dict) else None
    if isinstance(ids, list):
        sensitive = _sensitive_fact_ids(conn, ids)
        remaining = [item for item in ids if str(item) not in sensitive]
        if not remaining:
            return None
        cleaned["ids"] = remaining
    return cleaned


def _undo_projection(
    conn: sqlite3.Connection, steps: List[Dict[str, Any]]
) -> Dict[str, Any]:
    irreversible = False
    recipes = []
    for step in steps:
        if step.get("kind") not in _EXECUTED_KINDS:
            continue
        payload = step.get("payload") or {}
        if payload.get("idempotent_hit"):
            continue
        if payload.get("irreversible"):
            irreversible = True
        recipe = payload.get("undo")
        if recipe:
            filtered = _filter_recipe(conn, recipe)
            if filtered is not None:
                recipes.append(filtered)
    return {
        "available": bool(recipes) and not irreversible,
        "irreversible": irreversible,
        "recipes": recipes,
    }


def build_receipt(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    """Build a deterministic receipt for a finished run; mutate nothing."""
    run = _get_run_row(conn, run_id)
    if run["state"] not in FINAL_STATES:
        raise ValueError(
            "receipt requires a finished run: {} is {}".format(run_id, run["state"])
        )
    steps = get_steps(conn, run_id)
    projected = [
        {
            "seq": step["seq"],
            "kind": step["kind"],
            "hash": step["hash"],
            "prev_hash": step["prev_hash"],
            "payload_digest": _sha256(canonical(step["payload"])),
        }
        for step in steps
    ]
    body = {
        "version": VERSION,
        "run": {
            "id": run["id"],
            "plan_id": _plan_id(run),
            "state": run["state"],
            "created_at": run["created_at"],
            "finished_at": _finished_at(run),
        },
        "steps": projected,
        "chain_head": projected[-1]["hash"] if projected else "",
        "policy": {"risk": run["risk"], "decision": run["decision"]},
        "undo": _undo_projection(conn, steps),
    }
    body["digest"] = _sha256(canonical(body))
    return body


def to_json(data: Dict[str, Any]) -> str:
    """Canonical, byte-stable JSON encoding of a receipt (no trailing newline)."""
    return canonical(data)


def _short(value: Any, length: int = 16) -> str:
    text = "" if value is None else str(value)
    return text[:length] if len(text) > length else text


def to_markdown(data: Dict[str, Any]) -> str:
    """Human-readable rendering; the JSON form stays the machine contract."""
    run = data["run"]
    lines = [
        "# Receipt",
        "",
        "- run: `{}`".format(run["id"]),
        "- state: `{}`".format(run["state"]),
        "- plan_id: `{}`".format(run["plan_id"] or "-"),
        "- created_at: `{}`".format(run["created_at"]),
        "- finished_at: `{}`".format(run["finished_at"] or "-"),
        "- risk: `{}` decision: `{}`".format(
            data["policy"]["risk"], data["policy"]["decision"]
        ),
        "- steps: {}".format(len(data["steps"])),
        "- chain_head: `{}`".format(data["chain_head"] or "-"),
        "- digest: `{}`".format(data["digest"]),
        "",
        "## Steps",
        "",
        "| seq | kind | hash | payload_digest |",
        "|---|---|---|---|",
    ]
    for step in data["steps"]:
        lines.append(
            "| {} | {} | `{}` | `{}` |".format(
                step["seq"], step["kind"], _short(step["hash"]), _short(step["payload_digest"])
            )
        )
    if not data["steps"]:
        lines.append("| - | - | - | - |")
    undo = data["undo"]
    lines += [
        "",
        "## Undo",
        "",
        "- available: `{}`".format(str(undo["available"]).lower()),
        "- irreversible: `{}`".format(str(undo["irreversible"]).lower()),
        "- recipes: {}".format(len(undo["recipes"])),
    ]
    for recipe in undo["recipes"]:
        lines.append(
            "- `{}` {}".format(
                recipe.get("kind", "?"), canonical(recipe.get("target", {}))
            )
        )
    lines.append("")
    return "\n".join(lines)
