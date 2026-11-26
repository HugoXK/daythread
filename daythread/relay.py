"""Self-hosted relay for opaque, E2EE sync bundles."""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import ssl
import threading
import time
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
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
        retention_days: int = 0,
        device_tokens: Optional[Dict[str, str]] = None,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        self.token = token or secrets.token_urlsafe(32)
        self.retention_days = max(int(retention_days), 0)
        self.device_tokens = dict(device_tokens or {})
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((host, port), handler)
        if certfile and keyfile:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=certfile, keyfile=keyfile)
            self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return "http://{}:{}".format(host, port)

    def start(self) -> "RelayServer":
        self.cleanup()
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def cleanup(self) -> int:
        if self.retention_days <= 0:
            return 0
        cutoff = time.time() - self.retention_days * 86400
        removed = 0
        for path in Path(self.directory).glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

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
            if path == "/v1/bundles":
                if not self._authorized(owner.token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _list_bundles(owner.directory))
                return
            if path.startswith("/v1/bundles/"):
                device = path.split("/")[3]
                if not DEVICE_RE.match(device):
                    self._json(400, {"error": "invalid device id"})
                    return
                if not self._authorized(owner.token, device):
                    self._json(401, {"error": "unauthorized"})
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
            if not path.startswith("/v1/bundles/"):
                self._json(404, {"error": "not found"})
                return
            device = path.split("/")[3]
            if not DEVICE_RE.match(device):
                self._json(400, {"error": "invalid device id"})
                return
            if not self._authorized(owner.token, device):
                self._json(401, {"error": "unauthorized"})
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
            owner.cleanup()
            self._json(200, {"device_id": device, "size": size, "updated_at": utc_now()})

        def do_DELETE(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if not path.startswith("/v1/bundles/"):
                self._json(404, {"error": "not found"})
                return
            device = path.split("/")[3]
            if not DEVICE_RE.match(device):
                self._json(400, {"error": "invalid device id"})
                return
            if not self._authorized(owner.token, device):
                self._json(401, {"error": "unauthorized"})
                return
            path_obj = _bundle_path(owner.directory, device)
            if path_obj.exists():
                path_obj.unlink()
            self._json(200, {"device_id": device, "deleted": True})

        def _authorized(self, admin_token: str, device: Optional[str] = None) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return False
            supplied = header[len("Bearer "):]
            if hmac.compare_digest(supplied, admin_token):
                return True
            if device and device in owner.device_tokens:
                return hmac.compare_digest(supplied, owner.device_tokens[device])
            return False

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


def load_devices(path: Path) -> Dict[str, str]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def save_devices(path: Path, devices: Dict[str, str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(devices, ensure_ascii=False, indent=2, sort_keys=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)


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
