"""Connector SDK: manifests, mock providers, OAuth providers, and sync."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from . import obs
from .db import canonical, get_setting, json_load, row_to_dict, utc_now
from .errors import ProviderError, ValidationError

HTTP_OPENER: Optional[Callable[[urllib.request.Request], Any]] = None
HTTP_RETRIES = 3
HTTP_RETRY_BASE = 0.1
MAX_PAGES = 5


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
    action_handlers: Dict[str, Callable[..., Dict[str, Any]]] = field(default_factory=dict)


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
    base = (
        "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        "?singleEvents=true&orderBy=startTime&maxResults=25&timeMin={}".format(time_min)
    )
    items = []
    page_token = None
    for _ in range(MAX_PAGES):
        url = base
        if page_token:
            url += "&pageToken={}".format(quote(page_token))
        data = _http_json(url, token, opener)
        items.extend(data.get("items", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    result = []
    for event in items:
        start = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date")
        end = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date")
        result.append(
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
    return result


def _sync_google_gmail(
    now: datetime,
    token: Optional[Dict[str, Any]],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> List[Dict[str, Any]]:
    base = "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=10&q=is%3Aunread"
    messages = []
    page_token = None
    for _ in range(MAX_PAGES):
        url = base
        if page_token:
            url += "&pageToken={}".format(quote(page_token))
        listing = _http_json(url, token, opener)
        messages.extend(listing.get("messages", []))
        page_token = listing.get("nextPageToken")
        if not page_token:
            break
    items = []
    for message in messages[:10]:
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
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if not token or not token.get("access_token"):
        raise ValidationError("missing access token")
    headers = {
        "Authorization": "Bearer {}".format(token["access_token"]),
        "Accept": "application/json",
        "User-Agent": "daythread",
    }
    headers.update(extra_headers or {})
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    last_error = None
    for attempt in range(HTTP_RETRIES):
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
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < HTTP_RETRIES - 1:
                retry_after = 0
                try:
                    retry_after = float(exc.headers.get("Retry-After", "0") or 0)
                except (AttributeError, TypeError, ValueError):
                    retry_after = 0
                delay = min(max(retry_after, HTTP_RETRY_BASE * (2 ** attempt)), 2.0)
                time.sleep(delay)
                continue
            raise ProviderError("connector HTTP {}".format(exc.code))
        except Exception as exc:  # noqa: BLE001 - normalized provider error
            raise ProviderError("connector request failed: {}".format(exc))
    raise ProviderError("connector request failed: {}".format(last_error))



def _sync_microsoft_calendar(
    now: datetime,
    token: Optional[Dict[str, Any]],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> List[Dict[str, Any]]:
    start = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        "https://graph.microsoft.com/v1.0/me/calendarView"
        "?startDateTime={}&endDateTime={}&$top=25&$select=id,subject,start,end".format(
            quote(start), quote(end)
        )
    )
    items = []
    next_url = url
    for _ in range(MAX_PAGES):
        data = _http_json(
            next_url, token, opener, extra_headers={"Prefer": 'outlook.timezone="UTC"'}
        )
        for event in data.get("value", []):
            start_dt = (event.get("start") or {}).get("dateTime")
            end_dt = (event.get("end") or {}).get("dateTime")
            items.append(
                {
                    "kind": "calendar",
                    "external_id": str(event.get("id", "")),
                    "payload": {
                        "title": event.get("subject") or "(untitled)",
                        "start": start_dt,
                        "end": end_dt,
                    },
                }
            )
        next_url = data.get("@odata.nextLink")
        if not next_url:
            break
    return items


def _sync_microsoft_mail(
    now: datetime,
    token: Optional[Dict[str, Any]],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> List[Dict[str, Any]]:
    url = (
        "https://graph.microsoft.com/v1.0/me/messages"
        "?$filter=isRead%20eq%20false&$top=10"
        "&$select=id,from,subject,bodyPreview,receivedDateTime"
    )
    items = []
    next_url = url
    for _ in range(MAX_PAGES):
        data = _http_json(next_url, token, opener)
        for message in data.get("value", []):
            sender = (
                ((message.get("from") or {}).get("emailAddress") or {}).get("address", "")
            )
            items.append(
                {
                    "kind": "message",
                    "external_id": str(message.get("id", "")),
                    "payload": {
                        "sender": sender,
                        "subject": message.get("subject", ""),
                        "snippet": message.get("bodyPreview", ""),
                        "body": message.get("bodyPreview", ""),
                        "ts": message.get("receivedDateTime") or now.isoformat(),
                    },
                }
            )
        next_url = data.get("@odata.nextLink")
        if not next_url:
            break
    return items


def _raw_email(args: Dict[str, Any]) -> str:
    to = str(args.get("to", "")).strip()
    if not to:
        raise ValidationError("to is required")
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = str(args.get("subject", ""))
    message.set_content(str(args.get("body", "")))
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")



def _iso_from_feishu(value):
    if value is None:
        return None
    try:
        number = float(value)
        if number > 1e12:
            number = number / 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)


def _unix_from_value(value) -> str:
    if value is None:
        return str(int(datetime.now(timezone.utc).timestamp()))
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        pass
    try:
        return str(int(datetime.fromisoformat(str(value)).timestamp()))
    except ValueError:
        return str(int(datetime.now(timezone.utc).timestamp()))


def _feishu_calendar_sync(base: str):
    def sync(now, token, opener=None):
        primary = _http_json(
            "{}/open-apis/calendar/v4/calendars/primary".format(base), token, opener
        )
        data = primary.get("data") or {}
        calendar = data.get("calendar") or data
        calendar_id = (
            calendar.get("calendar_id")
            or data.get("calendar_id")
            or "primary"
        )
        start = int((now - timedelta(days=1)).timestamp())
        end = int((now + timedelta(days=7)).timestamp())
        url = (
            "{}/open-apis/calendar/v4/calendars/{}/events"
            "?start_time={}&end_time={}&page_size=50".format(
                base, quote(str(calendar_id), safe=""), start, end
            )
        )
        payload = _http_json(url, token, opener)
        items = (payload.get("data") or {}).get("items") or []
        result = []
        for event in items:
            result.append(
                {
                    "kind": "calendar",
                    "external_id": str(event.get("event_id", "")),
                    "payload": {
                        "title": event.get("summary") or "(untitled)",
                        "start": _iso_from_feishu(event.get("start_time")),
                        "end": _iso_from_feishu(event.get("end_time")),
                    },
                }
            )
        return result

    return sync


def _feishu_task_sync(base: str):
    def sync(now, token, opener=None):
        payload = _http_json(
            "{}/open-apis/task/v2/tasks?page_size=50".format(base), token, opener
        )
        items = (payload.get("data") or {}).get("items") or []
        result = []
        for task in items:
            due = task.get("due") or {}
            due_ts = due.get("timestamp") if isinstance(due, dict) else None
            result.append(
                {
                    "kind": "task",
                    "external_id": str(task.get("guid", "")),
                    "payload": {
                        "title": task.get("summary") or "(untitled)",
                        "status": "done" if task.get("completed_at") else "open",
                        "due": _iso_from_feishu(due_ts),
                    },
                }
            )
        return result

    return sync


def _feishu_calendar_create(base: str):
    def action(conn, home, token, args, opener=None):
        title = str(args.get("title", "")).strip()
        start = args.get("start")
        if not title or not start:
            raise ValidationError("title and start are required")
        calendar_id = str(args.get("calendar_id", "primary"))
        body = {
            "summary": title,
            "start_time": {
                "timestamp": _unix_from_value(start),
                "timezone": str(args.get("timezone", "UTC")),
            },
            "end_time": {
                "timestamp": _unix_from_value(args.get("end", start)),
                "timezone": str(args.get("timezone", "UTC")),
            },
        }
        return _http_json(
            "{}/open-apis/calendar/v4/calendars/{}/events".format(
                base, quote(calendar_id, safe="")
            ),
            token,
            opener,
            method="POST",
            body=body,
        )

    return action


def _feishu_task_create(base: str):
    def action(conn, home, token, args, opener=None):
        title = str(args.get("title", "")).strip()
        if not title:
            raise ValidationError("title is required")
        body = {"summary": title}
        if args.get("due"):
            body["due"] = {
                "timestamp": _unix_from_value(args["due"]),
                "is_all_day": bool(args.get("all_day", False)),
            }
        return _http_json(
            "{}/open-apis/task/v2/tasks".format(base),
            token,
            opener,
            method="POST",
            body=body,
        )

    return action


def _action_mock_write_note(conn, home, token, args, opener=None):
    path = Path(home) / "notes" / "mock-action.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(args.get("content", "mock action")), encoding="utf-8")
    return {"path": str(path)}


def _action_google_calendar_create(conn, home, token, args, opener=None):
    title = str(args.get("title", "")).strip()
    start = str(args.get("start", "")).strip()
    if not title or not start:
        raise ValidationError("title and start are required")
    body = {
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": str(args.get("end", start))},
    }
    return _http_json(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        token,
        opener,
        method="POST",
        body=body,
    )


def _action_google_calendar_move(conn, home, token, args, opener=None):
    event_id = str(args.get("event_id", "")).strip()
    start = str(args.get("start", "")).strip()
    if not event_id or not start:
        raise ValidationError("event_id and start are required")
    body = {
        "start": {"dateTime": start},
        "end": {"dateTime": str(args.get("end", start))},
    }
    return _http_json(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events/{}".format(
            quote(event_id, safe="")
        ),
        token,
        opener,
        method="PATCH",
        body=body,
    )


def _action_google_gmail_draft(conn, home, token, args, opener=None):
    raw = _raw_email(args)
    return _http_json(
        "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
        token,
        opener,
        method="POST",
        body={"message": {"raw": raw}},
    )


def _action_google_gmail_send(conn, home, token, args, opener=None):
    raw = _raw_email(args)
    return _http_json(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        token,
        opener,
        method="POST",
        body={"raw": raw},
    )


def _action_microsoft_calendar_create(conn, home, token, args, opener=None):
    title = str(args.get("title", "")).strip()
    start = str(args.get("start", "")).strip()
    if not title or not start:
        raise ValidationError("title and start are required")
    body = {
        "subject": title,
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": str(args.get("end", start)), "timeZone": "UTC"},
    }
    return _http_json(
        "https://graph.microsoft.com/v1.0/me/events",
        token,
        opener,
        method="POST",
        body=body,
    )


def _action_microsoft_calendar_move(conn, home, token, args, opener=None):
    event_id = str(args.get("event_id", "")).strip()
    start = str(args.get("start", "")).strip()
    if not event_id or not start:
        raise ValidationError("event_id and start are required")
    body = {
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": str(args.get("end", start)), "timeZone": "UTC"},
    }
    return _http_json(
        "https://graph.microsoft.com/v1.0/me/events/{}".format(
            quote(event_id, safe="")
        ),
        token,
        opener,
        method="PATCH",
        body=body,
    )


def _action_microsoft_mail_draft(conn, home, token, args, opener=None):
    to = str(args.get("to", "")).strip()
    if not to:
        raise ValidationError("to is required")
    body = {
        "subject": str(args.get("subject", "")),
        "body": {"contentType": "Text", "content": str(args.get("body", ""))},
        "toRecipients": [{"emailAddress": {"address": to}}],
    }
    return _http_json(
        "https://graph.microsoft.com/v1.0/me/messages",
        token,
        opener,
        method="POST",
        body=body,
    )


def _action_microsoft_mail_send(conn, home, token, args, opener=None):
    to = str(args.get("to", "")).strip()
    if not to:
        raise ValidationError("to is required")
    message = {
        "subject": str(args.get("subject", "")),
        "body": {"contentType": "Text", "content": str(args.get("body", ""))},
        "toRecipients": [{"emailAddress": {"address": to}}],
    }
    return _http_json(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        token,
        opener,
        method="POST",
        body={"message": message, "saveToSentItems": True},
    )


REGISTRY: Dict[str, ConnectorSpec] = {
    "mock.gcal": ConnectorSpec(
        id="mock.gcal",
        name="Mock Google Calendar",
        version="1.0.0",
        auth="mock",
        scopes=["calendar.read"],
        actions=[
            {"name": "list_events", "risk": "read"},
            {"name": "write_note", "risk": "external"},
        ],
        sync=_sync_gcal,
        description="Deterministic local calendar provider for development.",
        action_handlers={"write_note": _action_mock_write_note},
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
        version="1.1.0",
        auth="oauth2",
        scopes=["https://www.googleapis.com/auth/calendar"],
        actions=[
            {"name": "list_events", "risk": "read"},
            {"name": "create_event", "risk": "external"},
            {"name": "move_event", "risk": "external"},
        ],
        sync=_sync_google_calendar,
        description="Read and write primary Google Calendar events.",
        oauth_provider="google",
        action_handlers={
            "create_event": _action_google_calendar_create,
            "move_event": _action_google_calendar_move,
        },
    ),
    "google.gmail": ConnectorSpec(
        id="google.gmail",
        name="Gmail",
        version="1.1.0",
        auth="oauth2",
        scopes=[
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
        ],
        actions=[
            {"name": "list_messages", "risk": "read"},
            {"name": "create_draft", "risk": "external"},
            {"name": "send", "risk": "external"},
        ],
        sync=_sync_google_gmail,
        description="Read unread Gmail and create drafts or send mail.",
        oauth_provider="google",
        action_handlers={
            "create_draft": _action_google_gmail_draft,
            "send": _action_google_gmail_send,
        },
    ),
    "microsoft.calendar": ConnectorSpec(
        id="microsoft.calendar",
        name="Microsoft 365 Calendar",
        version="1.0.0",
        auth="oauth2",
        scopes=["offline_access", "User.Read", "Calendars.ReadWrite"],
        actions=[
            {"name": "list_events", "risk": "read"},
            {"name": "create_event", "risk": "external"},
            {"name": "move_event", "risk": "external"},
        ],
        sync=_sync_microsoft_calendar,
        description="Read and write Microsoft 365 calendar events.",
        oauth_provider="microsoft",
        action_handlers={
            "create_event": _action_microsoft_calendar_create,
            "move_event": _action_microsoft_calendar_move,
        },
    ),
    "microsoft.mail": ConnectorSpec(
        id="microsoft.mail",
        name="Microsoft 365 Mail",
        version="1.0.0",
        auth="oauth2",
        scopes=["offline_access", "User.Read", "Mail.ReadWrite", "Mail.Send"],
        actions=[
            {"name": "list_messages", "risk": "read"},
            {"name": "create_draft", "risk": "external"},
            {"name": "send", "risk": "external"},
        ],
        sync=_sync_microsoft_mail,
        description="Read unread Microsoft 365 mail, create drafts, or send.",
        oauth_provider="microsoft",
        action_handlers={
            "create_draft": _action_microsoft_mail_draft,
            "send": _action_microsoft_mail_send,
        },
    ),
    "feishu.calendar": ConnectorSpec(
        id="feishu.calendar",
        name="Feishu Calendar",
        version="1.0.0",
        auth="oauth2",
        scopes=["calendar:calendar"],
        actions=[
            {"name": "list_events", "risk": "read"},
            {"name": "create_event", "risk": "external"},
        ],
        sync=_feishu_calendar_sync("https://open.feishu.cn"),
        description="Read and write Feishu Calendar events.",
        oauth_provider="feishu",
        action_handlers={
            "create_event": _feishu_calendar_create("https://open.feishu.cn")
        },
    ),
    "feishu.tasks": ConnectorSpec(
        id="feishu.tasks",
        name="Feishu Tasks",
        version="1.0.0",
        auth="oauth2",
        scopes=["task:task:read", "task:task:write"],
        actions=[
            {"name": "list_tasks", "risk": "read"},
            {"name": "create_task", "risk": "external"},
        ],
        sync=_feishu_task_sync("https://open.feishu.cn"),
        description="Read and write Feishu Tasks.",
        oauth_provider="feishu",
        action_handlers={
            "create_task": _feishu_task_create("https://open.feishu.cn")
        },
    ),
    "lark.calendar": ConnectorSpec(
        id="lark.calendar",
        name="Lark Calendar",
        version="1.0.0",
        auth="oauth2",
        scopes=["calendar:calendar"],
        actions=[
            {"name": "list_events", "risk": "read"},
            {"name": "create_event", "risk": "external"},
        ],
        sync=_feishu_calendar_sync("https://open.larksuite.com"),
        description="Read and write Lark Calendar events.",
        oauth_provider="lark",
        action_handlers={
            "create_event": _feishu_calendar_create("https://open.larksuite.com")
        },
    ),
    "lark.tasks": ConnectorSpec(
        id="lark.tasks",
        name="Lark Tasks",
        version="1.0.0",
        auth="oauth2",
        scopes=["task:task:read", "task:task:write"],
        actions=[
            {"name": "list_tasks", "risk": "read"},
            {"name": "create_task", "risk": "external"},
        ],
        sync=_feishu_task_sync("https://open.larksuite.com"),
        description="Read and write Lark Tasks.",
        oauth_provider="lark",
        action_handlers={
            "create_task": _feishu_task_create("https://open.larksuite.com")
        },
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


def _get_token(
    conn: sqlite3.Connection,
    spec: ConnectorSpec,
    home: Optional[Any],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
    connector_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if spec.auth != "oauth2":
        return None
    if home is None:
        raise ValidationError("home is required for OAuth connectors")
    from .oauth import (
        TokenStore,
        connector_lock,
        ensure_token,
        get_oauth_config,
        is_expired,
    )

    target = connector_id or spec.id
    store = TokenStore(conn, home)
    with connector_lock(home, target):
        token = store.load(target)
        if not token:
            raise ValidationError(
                "connector {} is not authorized: dt connector auth {}".format(target, target)
            )
        granted = token.get("scope") or token.get("scopes")
        if granted:
            if isinstance(granted, str):
                granted = granted.split()
            if not oauth_scopes_ok(spec.scopes, list(granted)):
                raise ValidationError(
                    "connector {} is missing scopes; re-authorize: dt connector auth {}".format(
                        target, target
                    )
                )
        if is_expired(token):
            config = get_oauth_config(conn, spec.oauth_provider, spec.scopes, home)
            token = ensure_token(config, token, opener)
            store.save(target, token)
        return token


def oauth_scopes_ok(required, granted) -> bool:
    granted_set = set(granted)
    granted_short = {item.rsplit("/", 1)[-1] for item in granted_set}
    for scope in required:
        if scope in granted_set or scope.rsplit("/", 1)[-1] in granted_short:
            continue
        return False
    return True


def revoke(conn: sqlite3.Connection, home: Optional[Any], connector_id: str) -> Dict[str, Any]:
    spec = get_connector(connector_id)
    if spec.auth == "oauth2" and home is not None:
        from .oauth import TokenStore

        TokenStore(conn, home).delete(connector_id)
    conn.execute(
        "UPDATE connectors SET status = 'disconnected', token_ref = NULL WHERE id = ?",
        (connector_id,),
    )
    conn.commit()
    obs.audit(conn, "connector.revoked", actor="user", target=connector_id)
    return {"id": connector_id, "status": "disconnected"}


def call_action(
    conn: sqlite3.Connection,
    home: Optional[Any],
    connector_id: str,
    action: str,
    arguments: Dict[str, Any],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> Dict[str, Any]:
    spec = get_connector(connector_id)
    row = conn.execute("SELECT * FROM connectors WHERE id = ?", (connector_id,)).fetchone()
    if row is None or row["status"] != "connected":
        raise ValueError("connector {} is not connected".format(connector_id))
    handler = spec.action_handlers.get(action)
    if handler is None:
        raise ValidationError(
            "unknown action {} for {} (available: {})".format(
                action, connector_id, ", ".join(sorted(spec.action_handlers)) or "none"
            )
        )
    if not isinstance(arguments, dict):
        raise ValidationError("arguments must be an object")
    token = _get_token(conn, spec, home, opener, connector_id)
    result = handler(conn, home, token, arguments, opener)
    obs.incr(conn, "connector.actions")
    obs.audit(
        conn,
        "connector.action",
        actor="agent",
        target=connector_id,
        detail={"action": action},
    )
    return {"connector": connector_id, "action": action, "result": result}


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
    token = _get_token(conn, spec, home, opener, connector_id)

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
        elif kind == "task":
            conn.execute(
                """
                INSERT OR IGNORE INTO tasks(
                    id, title, status, due, source_run, created_at, updated_at,
                    external_id, connector
                ) VALUES(?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    payload.get("title", ""),
                    payload.get("status", "open"),
                    payload.get("due"),
                    utc_now(),
                    utc_now(),
                    external_id,
                    connector_id,
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
