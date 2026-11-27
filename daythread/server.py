"""Local-only HTTP API with bearer auth, rate limits, and pagination."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from . import (
    __version__,
    brief,
    connectors,
    memory,
    models,
    obs,
    proactivity,
    runtime,
    scheduler,
    webhook,
)
from . import migrations
from .db import connect, get_setting, init_db, is_initialized
from .errors import DaythreadError

MAX_BODY = 1_048_576


def ensure_token(home: Path, rotate: bool = False) -> str:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "api_token"
    if path.exists() and not rotate:
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


class ApiServer:
    def __init__(
        self,
        home: Path,
        db_path: Path,
        host: str = "127.0.0.1",
        port: int = 8765,
        rotate_token: bool = False,
    ) -> None:
        self.home = Path(home)
        self.db_path = Path(db_path)
        self.token = ensure_token(self.home, rotate=rotate_token)
        handler = _make_handler(self.home, self.db_path, self.token)
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> "ApiServer":
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=2)

    def serve_forever(self) -> None:
        self.httpd.serve_forever()


def _make_handler(home: Path, db_path: Path, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "daythread/{}".format(__version__)

        def log_message(self, fmt: str, *args: Any) -> None:
            obs.get_logger().debug(fmt % args)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            self._handle("POST")

        def do_DELETE(self) -> None:  # noqa: N802 - http.server API
            self._handle("DELETE")

        def _handle(self, method: str) -> None:
            request_id = self.headers.get("X-Request-Id") or uuid4().hex
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            conn = connect(db_path)
            try:
                if not is_initialized(conn):
                    init_db(conn)
                else:
                    migrations.migrate(conn)
                if not self._host_allowed(conn):
                    self._json(
                        400,
                        {"error": "invalid_host", "request_id": request_id},
                        request_id,
                    )
                    return
                if path == "/v1/health":
                    self._json(200, {"ok": True, "version": __version__}, request_id)
                    return
                if method == "POST" and path.startswith("/v1/webhooks/"):
                    self._handle_webhook(path, conn, request_id)
                    return
                if not self._authorized():
                    self._json(401, {"error": "unauthorized", "request_id": request_id}, request_id)
                    return
                try:
                    limit = int(get_setting(conn, "api.rate_limit_per_minute", "120") or 120)
                except (TypeError, ValueError):
                    limit = 120
                if not _rate_limit(conn, token, limit):
                    self._json(
                        429,
                        {"error": "rate_limited", "request_id": request_id},
                        request_id,
                        extra_headers={"Retry-After": "60"},
                    )
                    return
                if method == "GET" and path == "/metrics":
                    self._text(200, _prometheus(conn), request_id)
                    return
                body = self._read_body() if method == "POST" else {}
                status, payload = self._route(method, path, query, body, conn)
                self._json(status, payload, request_id)
            except DaythreadError as exc:
                self._json(
                    exc.status,
                    {"error": exc.code, "detail": str(exc), "request_id": request_id},
                    request_id,
                )
            except KeyError as exc:
                self._json(404, {"error": "not_found", "detail": str(exc), "request_id": request_id}, request_id)
            except ValueError as exc:
                self._json(400, {"error": "validation_error", "detail": str(exc), "request_id": request_id}, request_id)
            except Exception:  # noqa: BLE001 - never leak tracebacks
                obs.get_logger().exception("api request failed")
                self._json(
                    500,
                    {"error": "internal_error", "request_id": request_id},
                    request_id,
                )
            finally:
                conn.close()

        def _route(
            self,
            method: str,
            path: str,
            query: Dict[str, list],
            body: Dict[str, Any],
            conn: sqlite3.Connection,
        ) -> Tuple[int, Any]:
            if method == "GET" and path == "/v1/version":
                return 200, {"version": __version__}
            if method == "GET" and path == "/v1/openapi.json":
                return 200, _openapi()
            if method == "GET" and path == "/v1/today":
                evening = query.get("evening", ["0"])[0] in ("1", "true", "yes")
                data = brief.evening_brief(conn) if evening else brief.morning_brief(conn)
                return 200, data
            if method == "GET" and path == "/v1/facts":
                limit, offset = _paging(query)
                q = query.get("q", [""])[0]
                include_sensitive = query.get("include_sensitive", ["0"])[0] in (
                    "1",
                    "true",
                    "yes",
                )
                if q:
                    return 200, memory.search_facts(
                        conn, q, include_sensitive=include_sensitive
                    )
                return 200, memory.list_facts(
                    conn,
                    subject_type=query.get("type", [None])[0],
                    subject_id=query.get("subject", [None])[0],
                    include_sensitive=include_sensitive,
                    limit=limit,
                    offset=offset,
                )
            if method == "POST" and path == "/v1/facts":
                fact = memory.add_fact(
                    conn,
                    str(body.get("subject_type", "self")),
                    str(body.get("subject_id", "self")),
                    str(body.get("predicate", "")),
                    body.get("value"),
                    confidence=float(body.get("confidence", 0.8)),
                    sensitivity=str(body.get("sensitivity", "normal")),
                    tags=body.get("tags") or [],
                    metadata=body.get("metadata") or {},
                )
                return 201, fact
            if method == "GET" and path.startswith("/v1/facts/"):
                fact_id = path.split("/")[3]
                return 200, memory.get_fact(conn, fact_id)
            if method == "DELETE" and path.startswith("/v1/facts/"):
                fact_id = path.split("/")[3]
                return 200, memory.delete_fact(conn, fact_id)
            if method == "GET" and path == "/v1/runs":
                limit, offset = _paging(query)
                return 200, runtime.list_runs(conn, limit=limit, offset=offset)
            if method == "POST" and path == "/v1/runs":
                run = runtime.propose_plan(
                    conn,
                    home,
                    body.get("steps", []),
                    intent=body.get("intent"),
                    idempotency_key=self.headers.get("Idempotency-Key"),
                    force_approval=bool(body.get("force_approval", False)),
                )
                return 201, run
            if method == "GET" and path.startswith("/v1/runs/"):
                parts = path.split("/")
                run_id = parts[3]
                if len(parts) > 4 and parts[4] == "ledger":
                    from .ledger import get_steps

                    return 200, get_steps(conn, run_id)
                return 200, runtime.get_run(conn, run_id)
            if method == "POST" and path.startswith("/v1/runs/") and len(path.split("/")) == 5:
                parts = path.split("/")
                run_id, action = parts[3], parts[4]
                if action == "approve":
                    return 200, runtime.approve(conn, home, run_id)
                if action == "reject":
                    return 200, runtime.reject(conn, run_id)
                if action == "undo":
                    return 200, runtime.undo_run(conn, home, run_id)
                if action == "resume":
                    return 200, runtime.resume_run(conn, home, run_id)
                if action == "cancel":
                    return 200, runtime.cancel_run(conn, run_id)
            if method == "GET" and path == "/v1/candidates":
                limit, offset = _paging(query)
                return 200, proactivity.list_candidates(
                    conn,
                    status=query.get("status", ["new"])[0],
                    limit=limit,
                    offset=offset,
                )
            if method == "POST" and path == "/v1/feedback":
                return 200, proactivity.feedback(
                    conn, str(body.get("target", "")), str(body.get("action", ""))
                )
            if method == "GET" and path == "/v1/connectors":
                return 200, connectors.list_connectors(conn)
            if method == "POST" and path.startswith("/v1/connectors/") and path.endswith("/sync"):
                connector_id = path.split("/")[3]
                return 200, connectors.sync(conn, connector_id, home=home)
            if (
                method == "POST"
                and path.startswith("/v1/connectors/")
                and "/actions/" in path
            ):
                parts = path.split("/")
                connector_id = parts[3]
                action = parts[5]
                status = next(
                    (
                        item
                        for item in connectors.list_connectors(conn)
                        if item["id"] == connector_id
                    ),
                    None,
                )
                if status is None:
                    raise KeyError("unknown connector: {}".format(connector_id))
                if status["status"] != "connected":
                    raise ValueError(
                        "connector {} is not connected".format(connector_id)
                    )
                arguments = body.get("arguments") or {}
                run = runtime.propose_plan(
                    conn,
                    home,
                    [
                        {
                            "id": "s1",
                            "tool": "connector.call",
                            "args": {
                                "connector": connector_id,
                                "action": action,
                                "arguments": arguments,
                            },
                        }
                    ],
                    intent="connector {} {}".format(connector_id, action),
                    force_approval=True,
                )
                return 201, run
            if method == "GET" and path == "/v1/notifications":
                return 200, scheduler.list_notifications(conn, limit=_paging(query)[0])
            if method == "POST" and path == "/v1/tick":
                result = scheduler.tick(
                    conn,
                    home,
                    job=body.get("job"),
                )
                return 200, result
            if method == "GET" and path == "/v1/models":
                return 200, models.list_models(conn)
            if method == "POST" and path == "/v1/ask":
                response = models.ask(
                    conn,
                    str(body.get("text", "")),
                    task=str(body.get("task", "chat")),
                    use_cache=bool(body.get("use_cache", True)),
                    home=home,
                )
                return 200, response.to_dict()
            if method == "GET" and path == "/v1/metrics":
                return 200, obs.snapshot(conn)
            return 404, {"error": "not_found", "path": path}

        def _host_allowed(self, conn) -> bool:
            header = self.headers.get("Host", "")
            if not header:
                return False
            host = _hostname(header)
            allowed = get_setting(
                conn, "api.allowed_hosts", "127.0.0.1,localhost,::1"
            ) or "127.0.0.1,localhost,::1"
            values = {item.strip().lower() for item in allowed.split(",") if item.strip()}
            return host.lower() in values

        def _text(self, status: int, body: str, request_id: Optional[str] = None) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if request_id:
                self.send_header("X-Request-Id", request_id)
            self.end_headers()
            self.wfile.write(encoded)

        def _handle_webhook(self, path: str, conn, request_id: str) -> None:
            parts = path.split("/")
            if len(parts) != 4 or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", parts[3]):
                self._json(400, {"error": "invalid_webhook_source", "request_id": request_id}, request_id)
                return
            source = parts[3]
            try:
                secret = webhook.get_secret(conn, home, source)
            except KeyError:
                self._json(401, {"error": "unauthorized", "request_id": request_id}, request_id)
                return
            try:
                raw = self._read_raw()
            except ValueError as exc:
                self._json(400, {"error": str(exc), "request_id": request_id}, request_id)
                return
            max_age = int(get_setting(conn, "webhook.max_age_seconds", "300") or 300)
            if not webhook.verify(
                secret,
                raw,
                self.headers.get(webhook.TIMESTAMP_HEADER),
                self.headers.get(webhook.SIGNATURE_HEADER),
                max_age=max_age,
            ):
                self._json(401, {"error": "invalid_signature", "request_id": request_id}, request_id)
                return
            limit = int(get_setting(conn, "webhook.rate_limit_per_minute", "120") or 120)
            if not _rate_limit(conn, "webhook:{}".format(source), limit):
                self._json(
                    429,
                    {"error": "rate_limited", "request_id": request_id},
                    request_id,
                    extra_headers={"Retry-After": "60"},
                )
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "invalid_json", "request_id": request_id}, request_id)
                return
            if not isinstance(payload, dict):
                self._json(400, {"error": "payload_must_be_object", "request_id": request_id}, request_id)
                return
            dedupe = (
                payload.get("id")
                or payload.get("event_id")
                or payload.get("dedupe_key")
            )
            result = webhook.ingest(
                conn,
                source,
                payload,
                dedupe_key=str(dedupe) if dedupe else None,
            )
            self._json(202, result, request_id)

        def _read_raw(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > MAX_BODY:
                raise ValueError("request body too large")
            return self.rfile.read(length) if length else b""

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return False
            return hmac.compare_digest(header[len("Bearer "):], token)

        def _read_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > MAX_BODY:
                raise ValueError("request body too large")
            raw = self._read_raw() if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ValueError("invalid JSON body")
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _json(
            self,
            status: int,
            payload: Any,
            request_id: Optional[str] = None,
            extra_headers: Optional[Dict[str, str]] = None,
        ) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if request_id:
                self.send_header("X-Request-Id", request_id)
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def _hostname(header: str) -> str:
    if header.startswith("["):
        return header.split("]", 1)[0][1:]
    return header.split(":", 1)[0]


def _rate_limit(conn: sqlite3.Connection, token: str, limit: int, window: int = 60) -> bool:
    if limit <= 0:
        return True
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    now = int(time.time())
    start = now - (now % window)
    conn.execute(
        """
        INSERT INTO api_rate(key, window_start, count) VALUES(?, ?, 1)
        ON CONFLICT(key, window_start) DO UPDATE SET count = count + 1
        """,
        (key, start),
    )
    conn.commit()
    count = conn.execute(
        "SELECT count FROM api_rate WHERE key = ? AND window_start = ?", (key, start)
    ).fetchone()["count"]
    if count == 1:
        conn.execute("DELETE FROM api_rate WHERE window_start < ?", (start - 2 * window,))
        conn.commit()
    return int(count) <= limit


def _prometheus(conn: sqlite3.Connection) -> str:
    lines = ["# TYPE daythread_metric gauge"]
    for name, value in obs.snapshot(conn).items():
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        lines.append("daythread_{} {}".format(safe, value))
    return "\n".join(lines) + "\n"


def _paging(query: Dict[str, list]) -> Tuple[int, int]:
    try:
        limit = int(query.get("limit", ["50"])[0])
        offset = int(query.get("offset", ["0"])[0])
    except (TypeError, ValueError):
        raise ValueError("limit and offset must be integers")
    return max(0, min(limit, 200)), max(0, offset)


def _openapi() -> Dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Daythread local API", "version": __version__},
        "servers": [{"url": "http://127.0.0.1:8765"}],
        "components": {
            "securitySchemes": {
                "bearer": {"type": "http", "scheme": "bearer"}
            }
        },
        "security": [{"bearer": []}],
        "paths": {
            "/v1/health": {"get": {"summary": "Health check", "security": []}},
            "/v1/version": {"get": {"summary": "Version"}},
            "/v1/today": {"get": {"summary": "Morning or evening brief"}},
            "/v1/facts": {
                "get": {"summary": "List facts", "parameters": [_limit(), _offset()]},
                "post": {"summary": "Add a fact"},
            },
            "/v1/facts/{id}": {
                "get": {"summary": "Get a fact"},
                "delete": {"summary": "Tombstone a fact"},
            },
            "/v1/runs": {
                "get": {"summary": "List runs", "parameters": [_limit(), _offset()]},
                "post": {"summary": "Propose a plan"},
            },
            "/v1/runs/{id}": {"get": {"summary": "Get a run"}},
            "/v1/runs/{id}/ledger": {"get": {"summary": "Get ledger steps"}},
            "/v1/runs/{id}/approve": {"post": {"summary": "Approve a run"}},
            "/v1/runs/{id}/reject": {"post": {"summary": "Reject a run"}},
            "/v1/runs/{id}/undo": {"post": {"summary": "Undo a run"}},
            "/v1/runs/{id}/resume": {"post": {"summary": "Resume a run"}},
            "/v1/runs/{id}/cancel": {"post": {"summary": "Cancel a run"}},
            "/v1/candidates": {"get": {"summary": "List proactive candidates", "parameters": [_limit(), _offset()]}},
            "/v1/feedback": {"post": {"summary": "Record feedback"}},
            "/v1/connectors": {"get": {"summary": "List connectors"}},
            "/v1/connectors/{id}/sync": {"post": {"summary": "Sync a connector"}},
            "/v1/connectors/{id}/actions/{action}": {
                "post": {"summary": "Propose a connector action"}
            },
            "/v1/notifications": {"get": {"summary": "List notifications"}},
            "/v1/tick": {"post": {"summary": "Run a scheduler job"}},
            "/v1/models": {"get": {"summary": "Model gateway config and usage"}},
            "/v1/ask": {"post": {"summary": "Ask the configured model"}},
            "/v1/metrics": {"get": {"summary": "Metrics snapshot"}},
            "/metrics": {"get": {"summary": "Prometheus metrics"}},
            "/v1/webhooks/{source}": {
                "post": {"summary": "Signed webhook ingestion", "security": []}
            },
        },
    }


def _limit() -> Dict[str, Any]:
    return {"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": 200}}


def _offset() -> Dict[str, Any]:
    return {"name": "offset", "in": "query", "schema": {"type": "integer"}}
