"""Connector SDK: manifests, mock providers, OAuth providers, and sync."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from . import obs
from .db import canonical, get_setting, json_load, row_to_dict, utc_now
from .errors import ProviderError, ValidationError

HTTP_OPENER: Optional[Callable[[urllib.request.Request], Any]] = None


@dataclass
class ConnectorSpec:
    id: str
    name: str
    version: str
    auth: str
    scopes: List[str]
    actions: List[Dict[str, Any]]
    sync: Callable[..., List[Dict[str, Any]]]
    description: str = ""
    oauth_provider: str = ""


def _det_id(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:32]


def _at(day, hour: int, minute: int = 0) -> str:
    return datetime.combine(day, dtime(hour=hour, minute=minute), tzinfo=timezone.utc).isoformat()


def _sync_gcal(
    now: datetime,
    token: Optional[Dict[str, Any]] = None,
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> List[Dict[str, Any]]:
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


def _sync_gmail(
    now: datetime,
    token: Optional[Dict[str, Any]] = None,
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> List[Dict[str, Any]]:
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


def _sync_google_calendar(
    now: datetime,
    token: Optional[Dict[str, Any]],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> List[Dict[str, Any]]:
    time_min = quote((now - timedelta(days=1)).isoformat())
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        "?singleEvents=true&orderBy=startTime&maxResults=25&timeMin={}".format(time_min)
    )
    data = _http_json(url, token, opener)
    items = []
    for event in data.get("items", []):
        start = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date")
        end = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date")
        items.append(
            {
                "kind": "calendar",
                "external_id": str(event.get("id", "")),
                "payload": {
                    "title": event.get("summary") or "(untitled)",
                    "start": start,
                    "end": end,
                },
            }
        )
    return items


def _sync_google_gmail(
    now: datetime,
    token: Optional[Dict[str, Any]],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> List[Dict[str, Any]]:
    listing = _http_json(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=10&q=is%3Aunread",
        token,
        opener,
    )
    items = []
    for message in listing.get("messages", [])[:10]:
        message_id = str(message.get("id", ""))
        if not message_id:
            continue
        detail = _http_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/{}"
            "?format=metadata&metadataHeaders=From&metadataHeaders=Subject".format(
                quote(message_id)
            ),
            token,
            opener,
        )
        headers = {
            header.get("name", "").lower(): header.get("value", "")
            for header in (detail.get("payload") or {}).get("headers", [])
        }
        timestamp = now.isoformat()
        internal = detail.get("internalDate")
        if internal:
            try:
                timestamp = datetime.fromtimestamp(
                    int(internal) / 1000.0, tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError, OSError):
                pass
        items.append(
            {
                "kind": "message",
                "external_id": message_id,
                "payload": {
                    "sender": headers.get("from", ""),
                    "subject": headers.get("subject", ""),
                    "snippet": detail.get("snippet", ""),
                    "body": detail.get("snippet", ""),
                    "ts": timestamp,
                },
            }
        )
    return items


def _http_json(
    url: str,
    token: Optional[Dict[str, Any]],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> Dict[str, Any]:
    if not token or not token.get("access_token"):
        raise ValidationError("missing access token")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer {}".format(token["access_token"]),
            "Accept": "application/json",
            "User-Agent": "daythread",
        },
    )
    try:
        if opener is not None:
            raw = opener(request)
        elif HTTP_OPENER is not None:
            raw = HTTP_OPENER(request)
        else:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        if hasattr(raw, "read"):
            raw = raw.read()
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except urllib.error.HTTPError as exc:
        raise ProviderError("connector HTTP {}".format(exc.code))
    except Exception as exc:  # noqa: BLE001 - normalized provider error
        raise ProviderError("connector request failed: {}".format(exc))


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
    "google.calendar": ConnectorSpec(
        id="google.calendar",
        name="Google Calendar",
        version="1.0.0",
        auth="oauth2",
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        actions=[{"name": "list_events", "risk": "read"}],
        sync=_sync_google_calendar,
        description="Read primary Google Calendar events.",
        oauth_provider="google",
    ),
    "google.gmail": ConnectorSpec(
        id="google.gmail",
        name="Gmail",
        version="1.0.0",
        auth="oauth2",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        actions=[{"name": "list_messages", "risk": "read"}],
        sync=_sync_google_gmail,
        description="Read unread Gmail messages.",
        oauth_provider="google",
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
        "oauth_provider": spec.oauth_provider,
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
    if spec.auth == "oauth2":
        raise ValidationError(
            "OAuth connector {} must be authorized: dt connector auth {}".format(
                connector_id, connector_id
            )
        )
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


def disconnect(
    conn: sqlite3.Connection,
    connector_id: str,
    home: Optional[Any] = None,
) -> Dict[str, Any]:
    spec = get_connector(connector_id)
    if spec.auth == "oauth2" and home is not None:
        from .oauth import TokenStore

        TokenStore(conn, home).delete(connector_id)
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
    home: Optional[Any] = None,
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> Dict[str, Any]:
    spec = get_connector(connector_id)
    row = conn.execute("SELECT * FROM connectors WHERE id = ?", (connector_id,)).fetchone()
    if row is None or row["status"] != "connected":
        raise ValueError("connector {} is not connected".format(connector_id))
    moment = now or datetime.now(timezone.utc)
    token = None
    if spec.auth == "oauth2":
        if home is None:
            raise ValidationError("home is required to sync OAuth connectors")
        from .oauth import TokenStore, ensure_token, get_oauth_config, is_expired

        store = TokenStore(conn, home)
        token = store.load(connector_id)
        if not token:
            raise ValidationError(
                "connector {} is not authorized: dt connector auth {}".format(
                    connector_id, connector_id
                )
            )
        if is_expired(token):
            config = get_oauth_config(conn, spec.oauth_provider, spec.scopes, home)
            token = ensure_token(config, token, opener)
            store.save(connector_id, token)

    limit = int(get_setting(conn, "connector.max_items", "100") or 100)
    items = spec.sync(moment, token, opener)[:limit]
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
