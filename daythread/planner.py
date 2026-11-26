"""Deterministic plan construction, validation, and previews."""

from __future__ import annotations

import sqlite3
from collections import deque
from typing import Any, Dict, List

from . import policy
from .tools import get_tool

MAX_STEPS = 20
MAX_ATTEMPTS = 5
RISK_ORDER = {"read": 0, "write": 1, "external": 2, "destructive": 3}


def normalize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("a plan needs at least one step")
    if len(steps) > MAX_STEPS:
        raise ValueError("plan exceeds {} steps".format(MAX_STEPS))

    normalized: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            raise ValueError("step {} is not an object".format(index))
        step_id = str(raw.get("id") or "s{}".format(index))
        if step_id in seen:
            raise ValueError("duplicate step id: {}".format(step_id))
        seen.add(step_id)
        tool_name = str(raw.get("tool", "")).strip()
        if not tool_name:
            raise ValueError("step {} has no tool".format(step_id))
        get_tool(tool_name)
        args = raw.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError("step {} args must be an object".format(step_id))
        depends_on = raw.get("depends_on") or []
        if not isinstance(depends_on, list):
            raise ValueError("step {} depends_on must be a list".format(step_id))
        depends_on = [str(item) for item in depends_on]
        if step_id in depends_on:
            raise ValueError("step {} depends on itself".format(step_id))
        try:
            max_attempts = int(raw.get("max_attempts", 2))
        except (TypeError, ValueError):
            raise ValueError("step {} max_attempts must be an integer".format(step_id))
        max_attempts = max(1, min(max_attempts, MAX_ATTEMPTS))
        normalized.append(
            {
                "id": step_id,
                "tool": tool_name,
                "args": args,
                "depends_on": depends_on,
                "max_attempts": max_attempts,
            }
        )

    ids = {step["id"] for step in normalized}
    for step in normalized:
        for dep in step["depends_on"]:
            if dep not in ids:
                raise ValueError(
                    "step {} depends on unknown step {}".format(step["id"], dep)
                )
    return normalized


def toposort(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {step["id"]: step for step in steps}
    indegree = {step["id"]: len(step["depends_on"]) for step in steps}
    children: Dict[str, List[str]] = {step["id"]: [] for step in steps}
    for step in steps:
        for dep in step["depends_on"]:
            children[dep].append(step["id"])

    ready = deque(sorted([step_id for step_id, degree in indegree.items() if degree == 0]))
    order: List[str] = []
    while ready:
        step_id = ready.popleft()
        order.append(step_id)
        for child in sorted(children[step_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(order) != len(steps):
        raise ValueError("plan has a dependency cycle")
    return [by_id[step_id] for step_id in order]


def validate(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return toposort(normalize_steps(steps))


def preview(conn: sqlite3.Connection, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = validate(steps)
    items = []
    max_risk = "read"
    requires_approval = False
    denied = False
    for step in normalized:
        spec = get_tool(step["tool"])
        decision = policy.decide(conn, spec.risk)
        if RISK_ORDER[spec.risk] > RISK_ORDER[max_risk]:
            max_risk = spec.risk
        if decision == "ask":
            requires_approval = True
        if decision == "deny":
            denied = True
        items.append(
            {
                "id": step["id"],
                "tool": step["tool"],
                "risk": spec.risk,
                "capability": spec.capability,
                "decision": decision,
                "depends_on": step["depends_on"],
                "args": step["args"],
                "description": spec.description,
            }
        )
    return {
        "steps": items,
        "risk": max_risk,
        "requires_approval": requires_approval,
        "denied": denied,
        "order": [step["id"] for step in normalized],
    }


def from_json(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        steps = data.get("steps", [])
    elif isinstance(data, list):
        steps = data
    else:
        raise ValueError("plan must be an object with steps or a list")
    return validate(steps)


def from_intent(intent: str) -> List[Dict[str, Any]]:
    text = intent.strip()
    lower = text.lower()
    if not text:
        raise ValueError("intent is empty")

    if lower in ("brief", "morning brief", "evening brief"):
        return validate(
            [{"id": "s1", "tool": "brief.generate", "args": {"evening": lower.startswith("evening")}}]
        )
    if lower.startswith("remember "):
        return validate(
            [{"id": "s1", "tool": "memory.remember", "args": {"text": text[len("remember "):]}}]
        )
    if lower.startswith("search "):
        return validate(
            [{"id": "s1", "tool": "memory.search", "args": {"q": text[len("search "):]}}]
        )
    if lower.startswith("note "):
        return validate(
            [
                {
                    "id": "s1",
                    "tool": "note.write",
                    "args": {"path": "inbox.md", "content": text[len("note "):]},
                }
            ]
        )
    if lower.startswith("send "):
        payload = text[len("send "):]
        parts = [part.strip() for part in payload.split("|")]
        to = parts[0] if parts else ""
        subject = parts[1] if len(parts) > 1 else "(no subject)"
        body = parts[2] if len(parts) > 2 else ""
        if not to:
            raise ValueError("send needs a recipient: send <to> | <subject> | <body>")
        return validate(
            [
                {
                    "id": "s1",
                    "tool": "message.draft",
                    "args": {"to": to, "subject": subject, "body": body},
                },
                {
                    "id": "s2",
                    "tool": "message.send",
                    "args": {"to": to, "subject": subject, "body": body},
                    "depends_on": ["s1"],
                },
            ]
        )
    raise ValueError(
        "no template for intent; try: brief, remember <text>, search <q>, "
        "note <text>, send <to> | <subject> | <body>"
    )
