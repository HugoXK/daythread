"""Local-only HTTP API with bearer-token authentication."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__, brief, connectors, memory, obs, proactivity, runtime
from .db import connect, init_db, is_initialized

MAX_BODY = 1_048_576


def ensure_token(home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "api_token"
    if path.exists():
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
    ) -> None:
        self.home = Path(home)
        self.db_path = Path(db_path)
        self.token = ensure_token(self.home)
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
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            if path == "/v1/health":
                self._json(200, {"ok": True, "version": __version__})
                return
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            conn = connect(db_path)
            try:
                if not is_initialized(conn):
                    init_db(conn)
                body = self._read_body() if method in ("POST",) else {}
                status, payload = self._route(method, path, query, body, conn)
                self._json(status, payload)
            except KeyError as exc:
                self._json(404, {"error": str(exc)})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - never leak tracebacks
                self._json(500, {"error": type(exc).__name__, "detail": str(exc)})
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
            if method == "GET" and path == "/v1/today":
                evening = query.get("evening", ["0"])[0] in ("1", "true", "yes")
                data = brief.evening_brief(conn) if evening else brief.morning_brief(conn)
                return 200, data
            if method == "GET" and path == "/v1/facts":
                q = query.get("q", [""])[0]
                if q:
                    return 200, memory.search_facts(conn, q)
                return 200, memory.list_facts(
                    conn,
                    subject_type=query.get("type", [None])[0],
                    subject_id=query.get("subject", [None])[0],
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
                return 200, runtime.list_runs(conn, limit=int(query.get("limit", ["20"])[0]))
            if method == "POST" and path == "/v1/runs":
                run = runtime.propose_plan(
                    conn,
                    home,
                    body.get("steps", []),
                    intent=body.get("intent"),
                    idempotency_key=self.headers.get("Idempotency-Key"),
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
                return 200, proactivity.list_candidates(
                    conn, status=query.get("status", ["new"])[0]
                )
            if method == "POST" and path == "/v1/feedback":
                return 200, proactivity.feedback(
                    conn, str(body.get("target", "")), str(body.get("action", ""))
                )
            if method == "GET" and path == "/v1/connectors":
                return 200, connectors.list_connectors(conn)
            if method == "POST" and path.startswith("/v1/connectors/") and path.endswith("/sync"):
                connector_id = path.split("/")[3]
                return 200, connectors.sync(conn, connector_id)
            if method == "GET" and path == "/v1/metrics":
                return 200, obs.snapshot(conn)
            return 404, {"error": "not found", "path": path}

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

        def _json(self, status: int, payload: Any) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

    return Handler
