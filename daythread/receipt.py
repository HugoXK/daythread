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
* undo recipes keep only non-content fields (``kind``, ``path``, ``table``,
  ``id``, ``ids``, ``target``, and the ``connector``/``action`` identifiers
  needed to execute a connector recipe); ``previous`` and ``fields`` never
  leave the ledger, because they carry file or row content;
* what remains passes :func:`daythread.obs.redact`, so values under sensitive
  keys (token, secret, password, ...) become ``[REDACTED]``;
* ``facts.delete`` recipes drop ids of facts with ``sensitivity`` ``secret`` or
  ``health``; a recipe left with no ids is dropped entirely;
* ``approvals`` carries the real approver, decision and step time, so the
  receipt can prove who let an external step run;
* everything that remains is still derived from the run, so treat a receipt as
  sensitive as the run it describes.

``version`` is part of the external contract: bump it whenever the meaning of
an existing field changes, and document the old/new semantics in the PR. The
field set is frozen at 1 for this first release; ``approvals`` was added before
any receipt left the repository, so no released consumer sees a change.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import sqlite3
from typing import Any, Dict, List, Optional

from .db import canonical, json_load
from .ledger import get_steps
from .obs import redact
from .runtime import FINAL_STATES

VERSION = 1

_EXECUTED_KINDS = ("executed", "step.succeeded")
_SENSITIVE_FACT_LEVELS = ("secret", "health")
_ALLOWED_RECIPE_FIELDS = (
    "kind",
    "path",
    "table",
    "id",
    "ids",
    "target",
    "connector",
    "action",
)
_APPROVAL_KINDS = {"approval": "approved", "rejection": "rejected"}


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
    sensitive = set()
    chunk = 500  # keep below SQLite's bound-variable limit
    for start in range(0, len(wanted), chunk):
        part = wanted[start : start + chunk]
        rows = conn.execute(
            "SELECT id FROM facts WHERE id IN ({}) AND sensitivity IN ({})".format(
                ",".join("?" for _ in part),
                ",".join("?" for _ in _SENSITIVE_FACT_LEVELS),
            ),
            part + list(_SENSITIVE_FACT_LEVELS),
        ).fetchall()
        sensitive.update(row["id"] for row in rows)
    return sensitive


def _filter_recipe(conn: sqlite3.Connection, recipe: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Keep only non-content recipe fields, redacted; drop sensitive fact ids.

    ``previous`` / ``fields`` carry file or row content, so they never leave the
    ledger: a receipt is meant to be handed to someone else.
    """
    if not isinstance(recipe, dict):
        return None
    cleaned = redact(recipe)
    cleaned = {
        key: value for key, value in cleaned.items() if key in _ALLOWED_RECIPE_FIELDS
    }
    ids = cleaned.get("ids")
    if isinstance(ids, list):
        sensitive = _sensitive_fact_ids(conn, ids)
        remaining = [item for item in ids if str(item) not in sensitive]
        if not remaining:
            return None
        cleaned["ids"] = remaining
    return cleaned or None


def _approval_projection(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real approver / decision / time from approval and rejection steps."""
    approvals = []
    for step in steps:
        decision = _APPROVAL_KINDS.get(step.get("kind"))
        if decision is None:
            continue
        payload = step.get("payload") or {}
        approvals.append(
            {
                "decision": decision,
                "approver": payload.get("approver", ""),
                "ts": step["created_at"],
            }
        )
    return approvals


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
        "approvals": _approval_projection(steps),
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
        "## Approvals",
        "",
    ]
    if data["approvals"]:
        for item in data["approvals"]:
            lines.append(
                "- `{}` by `{}` at `{}`".format(
                    item["decision"], item["approver"], item["ts"]
                )
            )
    else:
        lines.append("- none")
    lines += [
        "",
        "## Steps",
        "",
        "| seq | kind | hash | prev_hash | payload_digest |",
        "|---|---|---|---|---|",
    ]
    for step in data["steps"]:
        lines.append(
            "| {} | {} | `{}` | `{}` | `{}` |".format(
                step["seq"],
                step["kind"],
                step["hash"],
                step["prev_hash"] or "-",
                step["payload_digest"],
            )
        )
    if not data["steps"]:
        lines.append("| - | - | - | - | - |")
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
        name = recipe.get("kind", "?")
        if recipe.get("connector") and recipe.get("action"):
            name = "{}.{}".format(recipe["connector"], recipe["action"])
        lines.append(
            "- `{}` {}".format(name, canonical(recipe.get("target", {})))
        )
    if undo["available"]:
        lines.append("- command: `dt undo {}`".format(shlex.quote(run["id"])))
    elif undo["irreversible"]:
        lines.append("- command: none (irreversible)")
    lines.append("")
    return "\n".join(lines)


_HEX_DIGITS = set("0123456789abcdef")
_STEP_ERROR = re.compile(r"step (\d+):")
_REQUIRED_FIELDS = ("version", "run", "steps", "chain_head", "policy", "undo", "digest")
_REQUIRED_STEP_FIELDS = ("seq", "kind", "hash", "prev_hash", "payload_digest")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in _HEX_DIGITS for char in value)
    )


def verify_receipt(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute a receipt's own integrity, strictly from the receipt itself.

    The receipt carries no payload (privacy), so a tampered ``payload_digest``
    is caught as a document-level ``digest`` mismatch, while a broken
    ``prev_hash`` link is located at its exact ``seq``. Step-level payload
    tampering in the local ledger is located by
    :func:`verify_against_ledger` instead.
    """
    errors: List[str] = []
    first_broken_seq: Optional[int] = None
    if not isinstance(data, dict):
        return {
            "ok": False,
            "errors": ["receipt must be a JSON object"],
            "first_broken_seq": None,
        }
    missing = [field for field in _REQUIRED_FIELDS if field not in data]
    if missing:
        errors.append("missing field(s): {}".format(", ".join(missing)))
    if data.get("version") != VERSION:
        errors.append(
            "unsupported receipt version: {} (expected {})".format(
                data.get("version"), VERSION
            )
        )
    steps = data.get("steps")
    if not isinstance(steps, list):
        errors.append("steps must be a list")
        steps = []
    prev = ""
    chain_unverifiable = False
    for step in steps:
        if not isinstance(step, dict):
            errors.append("step entry is not an object")
            chain_unverifiable = True
            continue
        seq = step.get("seq")
        missing_step = [field for field in _REQUIRED_STEP_FIELDS if field not in step]
        if missing_step:
            errors.append(
                "step {}: missing field(s): {}".format(seq, ", ".join(missing_step))
            )
            if first_broken_seq is None and isinstance(seq, int):
                first_broken_seq = seq
            chain_unverifiable = True
            prev = step["hash"] if isinstance(step.get("hash"), str) else ""
            continue
        if not chain_unverifiable and step["prev_hash"] != prev:
            errors.append(
                "step {}: prev_hash mismatch (expected {}, got {})".format(
                    seq, prev or "<empty>", step["prev_hash"]
                )
            )
            if first_broken_seq is None and isinstance(seq, int):
                first_broken_seq = seq
            chain_unverifiable = True
        if not _is_sha256(step["hash"]):
            errors.append("step {}: hash is not a sha256 hex digest".format(seq))
            if first_broken_seq is None and isinstance(seq, int):
                first_broken_seq = seq
            chain_unverifiable = True
        first_step = step is steps[0]
        if not _is_sha256(step["prev_hash"]) and not (first_step and step["prev_hash"] == ""):
            errors.append(
                "step {}: prev_hash is not a sha256 hex digest".format(seq)
            )
            if first_broken_seq is None and isinstance(seq, int):
                first_broken_seq = seq
        if not _is_sha256(step["payload_digest"]):
            errors.append(
                "step {}: payload_digest is not a sha256 hex digest".format(seq)
            )
            if first_broken_seq is None and isinstance(seq, int):
                first_broken_seq = seq
        prev = step["hash"]
    if data.get("chain_head") != prev:
        message = "chain_head mismatch (expected {}, got {})".format(
            prev or "<empty>", data.get("chain_head")
        )
        if steps:
            last = steps[-1]
            last_seq = last.get("seq") if isinstance(last, dict) else None
            if isinstance(last_seq, int):
                message += " at step {}".format(last_seq)
                if first_broken_seq is None:
                    first_broken_seq = last_seq
        errors.append(message)
    if "digest" in data:
        body = dict(data)
        body.pop("digest", None)
        expected = _sha256(canonical(body))
        if data.get("digest") != expected:
            errors.append(
                "digest mismatch: receipt body was modified (recorded {}, expected {})".format(
                    data.get("digest"), expected
                )
            )
    return {"ok": not errors, "errors": errors, "first_broken_seq": first_broken_seq}


def verify_against_ledger(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    """Recompute the live ledger chain and locate the first broken step."""
    from .ledger import verify_run

    ok, errors = verify_run(conn, run_id)
    first_broken_seq: Optional[int] = None
    for message in errors:
        match = _STEP_ERROR.search(message)
        if match and first_broken_seq is None:
            first_broken_seq = int(match.group(1))
    return {"ok": ok, "errors": list(errors), "first_broken_seq": first_broken_seq}
