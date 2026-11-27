"""Expose Daythread as an MCP stdio server for other agents."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO

from . import __version__, brief, connectors, memory, runtime
from .errors import ValidationError

READ_TOOLS = {
    "daythread.today",
    "daythread.memory_search",
    "daythread.task_list",
    "daythread.calendar_list",
    "daythread.approvals",
    "daythread.connectors",
}
WRITE_TOOLS = {"daythread.memory_remember", "daythread.propose_plan"}


def _schemas() -> List[Dict[str, Any]]:
    return [
        {
            "name": "daythread.today",
            "description": "Return the morning or evening brief.",
            "inputSchema": {
                "type": "object",
                "properties": {"evening": {"type": "boolean"}},
            },
        },
        {
            "name": "daythread.memory_search",
            "description": "Search structured memory facts.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "include_sensitive": {"type": "boolean"},
                },
                "required": ["q"],
            },
        },
        {
            "name": "daythread.task_list",
            "description": "List local tasks.",
            "inputSchema": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
        },
        {
            "name": "daythread.calendar_list",
            "description": "List local calendar events.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                },
            },
        },
        {
            "name": "daythread.approvals",
            "description": "List runs waiting for human approval.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "daythread.connectors",
            "description": "List connector status.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "daythread.memory_remember",
            "description": "Remember a structured fact or extract facts from text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "subject_type": {"type": "string"},
                    "subject_id": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {},
                    "confidence": {"type": "number"},
                },
            },
        },
        {
            "name": "daythread.propose_plan",
            "description": (
                "Propose a plan. Non-read steps always require human approval before "
                "execution."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "steps": {"type": "array", "items": {"type": "object"}},
                    "intent": {"type": "string"},
                    "taint": {"type": "string", "enum": ["trusted", "untrusted"]},
                },
                "required": ["steps"],
            },
        },
    ]


def serve(
    conn,
    home: Path,
    read_only: bool = True,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> None:
    inp = stdin or sys.stdin
    out = stdout or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue
        request_id = message["id"]
        method = message.get("method")
        params = message.get("params") or {}
        try:
            result = _handle(conn, home, read_only, method, params)
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:  # noqa: BLE001 - MCP error response
            code = -32603
            if isinstance(exc, ValidationError):
                code = -32602
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": str(exc)},
            }
        out.write(json.dumps(response, ensure_ascii=False) + "\n")
        out.flush()


def _handle(
    conn,
    home: Path,
    read_only: bool,
    method: Optional[str],
    params: Dict[str, Any],
) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "daythread", "version": __version__},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        tools = [schema for schema in _schemas() if not (read_only and schema["name"] in WRITE_TOOLS)]
        return {"tools": tools}
    if method == "resources/list":
        return {"resources": _resources(conn)}
    if method == "resources/read":
        return _read_resource(conn, str(params.get("uri", "")))
    if method == "prompts/list":
        return {"prompts": _prompts()}
    if method == "prompts/get":
        return _get_prompt(
            str(params.get("name", "")), params.get("arguments") or {}
        )
    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValidationError("arguments must be an object")
        if read_only and name in WRITE_TOOLS:
            raise ValidationError("tool is disabled in read-only mode: {}".format(name))
        data = _call(conn, home, name, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "isError": False,
        }
    raise ValidationError("unknown method: {}".format(method))


def _resources(conn) -> List[Dict[str, Any]]:
    resources = [
        {
            "uri": "daythread://today",
            "name": "Today",
            "description": "The current morning brief.",
            "mimeType": "application/json",
        }
    ]
    for fact in memory.list_facts(conn, limit=100):
        resources.append(
            {
                "uri": "daythread://memory/{}".format(fact["id"]),
                "name": "{} · {}".format(fact["subject_id"], fact["predicate"]),
                "description": fact["subject_type"],
                "mimeType": "application/json",
            }
        )
    return resources


def _read_resource(conn, uri: str) -> Dict[str, Any]:
    if uri == "daythread://today":
        data = brief.morning_brief(conn)
    elif uri.startswith("daythread://memory/"):
        fact = memory.get_fact(conn, uri.rsplit("/", 1)[-1])
        if fact.get("sensitivity") in ("secret", "health"):
            raise ValidationError("resource is sensitive and not exposed over MCP")
        data = fact
    else:
        raise ValidationError("unknown resource: {}".format(uri))
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(data, ensure_ascii=False),
            }
        ]
    }


def _prompts() -> List[Dict[str, Any]]:
    return [
        {
            "name": "daythread.morning",
            "description": "Summarize the day and propose at most three actions.",
        },
        {
            "name": "daythread.plan",
            "description": "Propose a Daythread plan for an intent.",
            "arguments": [
                {
                    "name": "intent",
                    "description": "What the user wants to accomplish.",
                    "required": True,
                }
            ],
        },
    ]


def _get_prompt(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name == "daythread.morning":
        text = (
            "Read the Daythread brief. Summarize what changed, then propose at most "
            "three concrete actions. Use daythread.propose_plan for anything that "
            "writes or sends."
        )
    elif name == "daythread.plan":
        intent = str(arguments.get("intent", "")).strip()
        if not intent:
            raise ValidationError("intent is required")
        text = (
            "Propose a Daythread plan for: {}. Prefer read tools first, then a "
            "single approval-gated write/send step. Explain the risk."
        ).format(intent)
    else:
        raise ValidationError("unknown prompt: {}".format(name))
    return {
        "description": name,
        "messages": [
            {"role": "user", "content": {"type": "text", "text": text}}
        ],
    }


def _call(conn, home: Path, name: str, arguments: Dict[str, Any]) -> Any:
    if name == "daythread.today":
        return (
            brief.evening_brief(conn)
            if arguments.get("evening")
            else brief.morning_brief(conn)
        )
    if name == "daythread.memory_search":
        query = str(arguments.get("q", "")).strip()
        facts = memory.search_facts(
            conn, query, include_sensitive=bool(arguments.get("include_sensitive", False))
        )
        limit = int(arguments.get("limit", 20))
        return facts[: max(1, min(limit, 50))]
    if name == "daythread.task_list":
        status = arguments.get("status")
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY due", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY status, due"
            ).fetchall()
        return [dict(row) for row in rows]
    if name == "daythread.calendar_list":
        clauses = []
        params: List[Any] = []
        if arguments.get("from"):
            clauses.append("date(start) >= ?")
            params.append(arguments["from"])
        if arguments.get("to"):
            clauses.append("date(start) <= ?")
            params.append(arguments["to"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            "SELECT * FROM calendar_events{} ORDER BY start".format(where), params
        ).fetchall()
        return [dict(row) for row in rows]
    if name == "daythread.approvals":
        return runtime.pending_runs(conn)
    if name == "daythread.connectors":
        return connectors.list_connectors(conn)
    if name == "daythread.memory_remember":
        return _remember(conn, arguments)
    if name == "daythread.propose_plan":
        steps = arguments.get("steps")
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except ValueError:
                raise ValidationError("steps must be an array")
        run = runtime.propose_plan(
            conn,
            home,
            steps or [],
            intent=arguments.get("intent"),
            force_approval=True,
            taint=str(arguments.get("taint", "untrusted")),
        )
        return {
            "id": run["id"],
            "state": run["state"],
            "risk": run["risk"],
            "decision": run["decision"],
            "plan": run.get("plan"),
        }
    raise ValidationError("unknown tool: {}".format(name))


def _remember(conn, arguments: Dict[str, Any]) -> Any:
    if arguments.get("value") is not None and arguments.get("predicate"):
        fact = memory.add_fact(
            conn,
            str(arguments.get("subject_type", "self")),
            str(arguments.get("subject_id", "self")),
            str(arguments["predicate"]),
            arguments.get("value"),
            confidence=float(arguments.get("confidence", 0.8)),
            source="mcp",
        )
        return {"created": [fact], "existing": []}
    text = str(arguments.get("text", "")).strip()
    if not text:
        raise ValidationError("text or predicate/value is required")
    candidates = memory.extract_facts(text)
    if not candidates:
        raise ValidationError("nothing structured found in text")
    facts = memory.apply_extracted(conn, candidates, created_by="mcp")
    return {"created": facts, "existing": []}
