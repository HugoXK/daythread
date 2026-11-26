"""Connector SDK with manifests, mock providers, and sync."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from . import obs
from .db import canonical, get_setting, json_load, row_to_dict, utc_now


@dataclass
class ConnectorSpec:
    id: str
    name: str
    version: str
    auth: str
    scopes: List[str]
    actions: List[Dict[str, Any]]
    sync: Callable[[datetime], List[Dict[str, Any]]]
    description: str = ""


def _det_id(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:32]


def _at(day, hour: int, minute: int = 0) -> str:
    return datetime.combine(day, dtime(hour=hour, minute=minute), tzinfo=timezone.utc).isoformat()


def _sync_gcal(now: datetime) -> List[Dict[str, Any]]:
    today = now.date()
    tomorrow = today + timedelta(days=1)
    return [
        {
            "kind": "calendar",
            "external_id": "mock-gcal-weekly-sync",
            "payload": {
                "title": "Weekly sync",
                "start": _at(today, 10, 0),
                "end": _at(today, 10, 30),
            },
        },
        {
            "kind": "calendar",
            "external_id": "mock-gcal-design-review",
            "payload": {
                "title": "Design review",
                "start": _at(tomorrow, 15, 0),
                "end": _at(tomorrow, 16, 0),
            },
        },
    ]


def _sync_gmail(now: datetime) -> List[Dict[str, Any]]:
    return [
        {
            "kind": "message",
            "external_id": "mock-gmail-deck-feedback",
            "payload": {
                "sender": "maya@example.com",
                "subject": "Deck feedback",
                "snippet": "Looks good. One change before Friday.",
                "body": "Looks good. One change before Friday: tighten the opening slide.",
                "ts": now.isoformat(),
            },
        },
        {
            "kind": "message",
            "external_id": "mock-gmail-investor",
            "payload": {
                "sender": "investor@example.com",
                "subject": "Follow-up",
                "snippet": "Can we move the call?",
                "body": "Can we move Thursday's call to the afternoon?",
                "ts": now.isoformat(),
            },
        },
    ]


REGISTRY: Dict[str, ConnectorSpec] = {
    "mock.gcal": ConnectorSpec(
        id="mock.gcal",
        name="Mock Google Calendar",
        version="1.0.0",
        auth="mock",
        scopes=["calendar.read"],
        actions=[{"name": "list_events", "risk": "read"}],
        sync=_sync_gcal,
        description="Deterministic local calendar provider for development.",
    ),
    "mock.gmail": ConnectorSpec(
        id="mock.gmail",
        name="Mock Gmail",
        version="1.0.0",
        auth="mock",
        scopes=["mail.read"],
        actions=[{"name": "list_messages", "risk": "read"}],
        sync=_sync_gmail,
        description="Deterministic local mail provider for development.",
    ),
}


def get_connector(connector_id: str) -> ConnectorSpec:
    try:
        return REGISTRY[connector_id]
    except KeyError:
        raise KeyError(
            "unknown connector: {} (available: {})".format(
                connector_id, ", ".join(sorted(REGISTRY))
            )
        )


def manifest(connector_id: str) -> Dict[str, Any]:
    spec = get_connector(connector_id)
    return {
        "id": spec.id,
        "name": spec.name,
        "version": spec.version,
        "auth": spec.auth,
        "scopes": spec.scopes,
        "actions": spec.actions,
        "description": spec.description,
    }


def list_connectors(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = {
        row["id"]: dict(row)
        for row in conn.execute("SELECT * FROM connectors").fetchall()
    }
    result = []
    for connector_id, spec in sorted(REGISTRY.items()):
        state = rows.get(connector_id, {})
        result.append(
            {
                **manifest(connector_id),
                "status": state.get("status", "available"),
                "connected_at": state.get("connected_at"),
                "last_sync": state.get("last_sync"),
                "error": state.get("error"),
            }
        )
    return result


def connect(
    conn: sqlite3.Connection,
    connector_id: str,
    token_ref: Optional[str] = None,
) -> Dict[str, Any]:
    spec = get_connector(connector_id)
    if spec.auth != "mock" and not token_ref:
        raise ValueError("connector {} requires a token_ref".format(connector_id))
    now = utc_now()
    row = conn.execute("SELECT * FROM connectors WHERE id = ?", (connector_id,)).fetchone()
    connected_at = row["connected_at"] if row and row["connected_at"] else now
    conn.execute(
        """
        INSERT OR REPLACE INTO connectors(id, version, status, scopes, connected_at, last_sync, token_ref, error)
        VALUES(?, ?, 'connected', ?, ?, ?, ?, NULL)
        """,
        (
            connector_id,
            spec.version,
            canonical(spec.scopes),
            connected_at,
            row["last_sync"] if row else None,
            token_ref,
        ),
    )
    conn.commit()
    obs.audit(conn, "connector.connected", actor="user", target=connector_id, detail={"auth": spec.auth})
    return {"id": connector_id, "status": "connected", "scopes": spec.scopes, "connected_at": connected_at}


def disconnect(conn: sqlite3.Connection, connector_id: str) -> Dict[str, Any]:
    get_connector(connector_id)
    conn.execute(
        "UPDATE connectors SET status = 'disconnected', token_ref = NULL WHERE id = ?",
        (connector_id,),
    )
    conn.commit()
    obs.audit(conn, "connector.disconnected", actor="user", target=connector_id)
    return {"id": connector_id, "status": "disconnected"}


def sync(
    conn: sqlite3.Connection,
    connector_id: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    spec = get_connector(connector_id)
    row = conn.execute("SELECT * FROM connectors WHERE id = ?", (connector_id,)).fetchone()
    if row is None or row["status"] != "connected":
        raise ValueError("connector {} is not connected".format(connector_id))
    moment = now or datetime.now(timezone.utc)
    limit = int(get_setting(conn, "connector.max_items", "100") or 100)
    items = spec.sync(moment)[:limit]
    inserted = 0
    for item in items:
        kind = item["kind"]
        external_id = item["external_id"]
        payload = item["payload"]
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO connector_items(id, connector, kind, external_id, payload, ts)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                _det_id("{}:{}:{}".format(connector_id, kind, external_id)),
                connector_id,
                kind,
                external_id,
                canonical(payload),
                utc_now(),
            ),
        )
        if cursor.rowcount == 0:
            continue
        inserted += 1
        item_id = _det_id("{}:{}".format(connector_id, external_id))
        if kind == "calendar":
            conn.execute(
                """
                INSERT OR IGNORE INTO calendar_events(id, title, start, end, source_run, created_at, external_id, connector)
                VALUES(?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    item_id,
                    payload.get("title", ""),
                    payload.get("start"),
                    payload.get("end"),
                    utc_now(),
                    external_id,
                    connector_id,
                ),
            )
        elif kind == "message":
            conn.execute(
                """
                INSERT OR IGNORE INTO messages(id, external_id, thread_id, sender, subject, snippet, body, ts, taint)
                VALUES(?, ?, NULL, ?, ?, ?, ?, ?, 'untrusted')
                """,
                (
                    item_id,
                    external_id,
                    payload.get("sender"),
                    payload.get("subject"),
                    payload.get("snippet"),
                    payload.get("body"),
                    payload.get("ts"),
                ),
            )
    conn.execute(
        "UPDATE connectors SET last_sync = ?, error = NULL WHERE id = ?",
        (utc_now(), connector_id),
    )
    conn.commit()
    obs.incr(conn, "connector.items.synced", inserted)
    obs.audit(conn, "connector.synced", actor="agent", target=connector_id, detail={"items": inserted})
    return {"id": connector_id, "items": len(items), "inserted": inserted}


def list_items(conn: sqlite3.Connection, connector_id: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    if kind:
        rows = conn.execute(
            "SELECT * FROM connector_items WHERE connector = ? AND kind = ? ORDER BY ts DESC",
            (connector_id, kind),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM connector_items WHERE connector = ? ORDER BY ts DESC",
            (connector_id,),
        ).fetchall()
    items = []
    for row in rows:
        item = row_to_dict(row)
        item["payload"] = json_load(item["payload"])
        items.append(item)
    return items


def list_messages(conn: sqlite3.Connection, limit: int = 20) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM messages ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()]
