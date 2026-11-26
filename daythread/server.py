"""Local-only HTTP API with bearer auth, rate limits, and pagination."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
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
)
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


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: Dict[str, Deque[float]] = {}

    def check(self, key: str, limit: int, window: float = 60.0) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


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
        self.limiter = RateLimiter()
        handler = _make_handler(self.home, self.db_path, self.token, self.limiter)
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


def _make_handler(home: Path, db_path: Path, token: str, limiter: RateLimiter):
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
            if path == "/v1/health":
                self._json(200, {"ok": True, "version": __version__}, request_id)
                return
            if not self._authorized():
                self._json(401, {"error": "unauthorized", "request_id": request_id}, request_id)
                return
            conn = connect(db_path)
            try:
                if not is_initialized(conn):
                    init_db(conn)
                try:
                    limit = int(get_setting(conn, "api.rate_limit_per_minute", "120") or 120)
                except (TypeError, ValueError):
                    limit = 120
                if not limiter.check(token, limit):
                    self._json(
                        429,
                        {"error": "rate_limited", "request_id": request_id},
                        request_id,
                        extra_headers={"Retry-After": "60"},
                    )
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
            except Exception as exc:  # noqa: BLE001 - never leak tracebacks
                self._json(
                    500,
                    {"error": type(exc).__name__, "detail": str(exc), "request_id": request_id},
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
                if q:
                    return 200, memory.search_facts(conn, q)
                return 200, memory.list_facts(
                    conn,
                    subject_type=query.get("type", [None])[0],
                    subject_id=query.get("subject", [None])[0],
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
                )
                return 200, response.to_dict()
            if method == "GET" and path == "/v1/metrics":
                return 200, obs.snapshot(conn)
            return 404, {"error": "not_found", "path": path}

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return False
            return hmac.compare_digest(header[len("Bearer "):], token)

        def _read_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > MAX_BODY:
                raise ValueError("request body too large")
            raw = self.rfile.read(length) if length else b"{}"
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
            "/v1/notifications": {"get": {"summary": "List notifications"}},
            "/v1/tick": {"post": {"summary": "Run a scheduler job"}},
            "/v1/models": {"get": {"summary": "Model gateway config and usage"}},
            "/v1/ask": {"post": {"summary": "Ask the configured model"}},
            "/v1/metrics": {"get": {"summary": "Metrics snapshot"}},
        },
    }


def _limit() -> Dict[str, Any]:
    return {"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": 200}}


def _offset() -> Dict[str, Any]:
    return {"name": "offset", "in": "query", "schema": {"type": "integer"}}
