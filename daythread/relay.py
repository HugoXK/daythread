"""Self-hosted relay for opaque, E2EE sync bundles."""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from .db import utc_now

MAX_BODY = 8 * 1024 * 1024
DEVICE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
BUNDLE_FORMAT = "daythread.sync.v1"


class RelayServer:
    def __init__(
        self,
        directory: Path,
        token: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 8788,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        self.token = token or secrets.token_urlsafe(32)
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return "http://{}:{}".format(host, port)

    def start(self) -> "RelayServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread:
            self._thread.join(timeout=2)

    def serve_forever(self) -> None:
        self.httpd.serve_forever()


def _make_handler(owner: "RelayServer"):
    class Handler(BaseHTTPRequestHandler):
        server_version = "daythread-relay/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/v1/health":
                self._json(200, {"ok": True})
                return
            if not self._authorized(owner.token):
                self._json(401, {"error": "unauthorized"})
                return
            if path == "/v1/bundles":
                self._json(200, _list_bundles(owner.directory))
                return
            if path.startswith("/v1/bundles/"):
                device = path.split("/")[3]
                if not DEVICE_RE.match(device):
                    self._json(400, {"error": "invalid device id"})
                    return
                bundle = _read_bundle(owner.directory, device)
                if bundle is None:
                    self._json(404, {"error": "not found"})
                    return
                self._json(200, bundle)
                return
            self._json(404, {"error": "not found"})

        def do_PUT(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if not self._authorized(owner.token):
                self._json(401, {"error": "unauthorized"})
                return
            if not path.startswith("/v1/bundles/"):
                self._json(404, {"error": "not found"})
                return
            device = path.split("/")[3]
            if not DEVICE_RE.match(device):
                self._json(400, {"error": "invalid device id"})
                return
            try:
                bundle = self._read_body()
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            if bundle.get("format") != BUNDLE_FORMAT:
                self._json(400, {"error": "unsupported bundle format"})
                return
            for key in ("salt", "nonce", "ciphertext"):
                if key not in bundle:
                    self._json(400, {"error": "missing field: {}".format(key)})
                    return
            size = _write_bundle(owner.directory, device, bundle)
            self._json(200, {"device_id": device, "size": size, "updated_at": utc_now()})

        def _authorized(self, token: str) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return False
            return hmac.compare_digest(header[len("Bearer "):], token)

        def _read_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("invalid body length")
            raw = self.rfile.read(length)
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
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def _bundle_path(directory: Path, device: str) -> Path:
    return Path(directory) / "{}.json".format(device)


def _write_bundle(directory: Path, device: str, bundle: Dict[str, Any]) -> int:
    path = _bundle_path(directory, device)
    temp = path.with_name("{}.{}.tmp".format(device, uuid4().hex))
    data = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(str(temp), str(path))
    return len(data)


def _read_bundle(directory: Path, device: str) -> Optional[Dict[str, Any]]:
    path = _bundle_path(directory, device)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _list_bundles(directory: Path) -> Dict[str, Any]:
    items = []
    for path in sorted(Path(directory).glob("*.json")):
        stat = path.stat()
        items.append(
            {
                "device_id": path.stem,
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).replace(microsecond=0).isoformat(),
            }
        )
    return {"bundles": items}
