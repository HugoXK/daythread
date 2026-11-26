"""OAuth2 authorization-code + PKCE flow with encrypted token storage."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from .crypto import SecretVault
from .db import get_setting
from .errors import DaythreadError, ProviderError, ValidationError

PROVIDERS = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
    }
}


@dataclass
class OAuthConfig:
    provider: str
    auth_url: str
    token_url: str
    client_id: str
    client_secret: Optional[str]
    scopes: List[str]
    redirect_host: str = "127.0.0.1"
    redirect_port: int = 0


class CallbackServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.result: Optional[Dict[str, Optional[str]]] = None
        self.event = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                params = parse_qs(parsed.query)
                owner.result = {
                    "code": params.get("code", [None])[0],
                    "state": params.get("state", [None])[0],
                    "error": params.get("error", [None])[0],
                }
                body = b"Authorization received. You can close this window."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                owner.event.set()

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        self.httpd = HTTPServer((host, port), Handler)
        self.port = int(self.httpd.server_address[1])
        self._thread: Optional[threading.Thread] = None

    @property
    def redirect_uri(self) -> str:
        return "http://{}:{}/callback".format(self.host, self.port)

    def start(self) -> "CallbackServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread:
            self._thread.join(timeout=2)

    def wait_for_code(self, timeout: float = 300.0) -> Dict[str, Optional[str]]:
        if not self.event.wait(timeout):
            raise DaythreadError("OAuth callback timed out after {:.0f}s".format(timeout))
        return self.result or {}


class TokenStore:
    def __init__(self, conn, home: Path) -> None:
        self.conn = conn
        self.vault = SecretVault(conn, Path(home))

    def save(self, connector_id: str, token: Dict[str, Any]) -> None:
        self.vault.set("oauth.{}".format(connector_id), json.dumps(token, sort_keys=True))

    def load(self, connector_id: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self.vault.get("oauth.{}".format(connector_id)))
        except KeyError:
            return None

    def delete(self, connector_id: str) -> None:
        self.vault.delete("oauth.{}".format(connector_id))


def pkce_pair() -> tuple:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(
    config: OAuthConfig,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    extra: Optional[Dict[str, str]] = None,
) -> str:
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    params.update(extra or {})
    return "{}?{}".format(config.auth_url, urlencode(params))


def get_oauth_config(
    conn,
    provider: str,
    scopes: List[str],
    home: Path,
) -> OAuthConfig:
    if provider not in PROVIDERS:
        raise ValidationError("unknown OAuth provider: {}".format(provider))
    base = PROVIDERS[provider]
    client_id = get_setting(conn, "oauth.{}.client_id".format(provider), "") or ""
    if not client_id:
        raise ValidationError(
            "set oauth.{}.client_id first (dt config set oauth.{}.client_id ...)".format(
                provider, provider
            )
        )
    secret_ref = get_setting(conn, "oauth.{}.client_secret_ref".format(provider), "") or ""
    client_secret = _resolve_ref(conn, home, secret_ref)
    redirect_host = get_setting(conn, "oauth.redirect_host", "127.0.0.1") or "127.0.0.1"
    redirect_port = int(get_setting(conn, "oauth.redirect_port", "0") or 0)
    return OAuthConfig(
        provider=provider,
        auth_url=str(get_setting(conn, "oauth.{}.auth_url".format(provider), base["auth_url"])),
        token_url=str(get_setting(conn, "oauth.{}.token_url".format(provider), base["token_url"])),
        client_id=client_id,
        client_secret=client_secret,
        scopes=list(scopes),
        redirect_host=redirect_host,
        redirect_port=redirect_port,
    )


def exchange_code(
    config: OAuthConfig,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> Dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config.client_id,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    if config.client_secret:
        data["client_secret"] = config.client_secret
    return _token_request(config.token_url, data, opener)


def refresh_token(
    config: OAuthConfig,
    token: Dict[str, Any],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> Dict[str, Any]:
    refresh = token.get("refresh_token")
    if not refresh:
        raise DaythreadError("token has no refresh_token")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": config.client_id,
    }
    if config.client_secret:
        data["client_secret"] = config.client_secret
    refreshed = _token_request(config.token_url, data, opener)
    if "refresh_token" not in refreshed:
        refreshed["refresh_token"] = refresh
    return refreshed


def is_expired(token: Dict[str, Any], skew_seconds: int = 60) -> bool:
    expires_at = token.get("expires_at")
    if not expires_at:
        return False
    try:
        expires = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return False
    return expires <= datetime.now(timezone.utc) + timedelta(seconds=skew_seconds)


def ensure_token(
    config: OAuthConfig,
    token: Dict[str, Any],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> Dict[str, Any]:
    if is_expired(token):
        return refresh_token(config, token, opener)
    return token


def authorize(
    conn,
    home: Path,
    connector_id: str,
    open_browser: bool = False,
    timeout: float = 300.0,
    on_url: Optional[Callable[[str], None]] = None,
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> Dict[str, Any]:
    from .connectors import get_connector
    from .crypto import ensure_crypto

    ensure_crypto()

    spec = get_connector(connector_id)
    if spec.auth != "oauth2" or not spec.oauth_provider:
        raise ValidationError("connector {} does not use OAuth".format(connector_id))
    config = get_oauth_config(conn, spec.oauth_provider, spec.scopes, home)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    server = CallbackServer(config.redirect_host, config.redirect_port).start()
    try:
        url = build_authorization_url(
            config,
            server.redirect_uri,
            state,
            challenge,
            extra={"access_type": "offline", "prompt": "consent"},
        )
        if on_url:
            on_url(url)
        if open_browser:
            webbrowser.open(url)
        result = server.wait_for_code(timeout)
    finally:
        server.stop()
    if result.get("error"):
        raise DaythreadError("OAuth error: {}".format(result["error"]))
    if not result.get("code"):
        raise DaythreadError("OAuth callback did not include a code")
    if result.get("state") != state:
        raise DaythreadError("OAuth state mismatch")
    token = exchange_code(config, str(result["code"]), verifier, server.redirect_uri, opener)
    TokenStore(conn, home).save(connector_id, token)
    conn.execute(
        """
        INSERT OR REPLACE INTO connectors(id, version, status, scopes, connected_at, last_sync, token_ref, error)
        VALUES(?, ?, 'connected', ?, ?, NULL, NULL, NULL)
        """,
        (
            connector_id,
            spec.version,
            json.dumps(spec.scopes),
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        ),
    )
    conn.commit()
    return token


def _token_request(
    url: str,
    data: Dict[str, str],
    opener: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        if opener is not None:
            raw = opener(request)
        else:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        if hasattr(raw, "read"):
            raw = raw.read()
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise ProviderError("OAuth token HTTP {}: {}".format(exc.code, detail))
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("OAuth token request failed: {}".format(exc))
    if "access_token" not in payload:
        raise ProviderError("OAuth token response missing access_token")
    if "expires_in" in payload:
        try:
            expires = datetime.now(timezone.utc) + timedelta(seconds=int(payload["expires_in"]))
            payload["expires_at"] = expires.replace(microsecond=0).isoformat()
        except (TypeError, ValueError):
            pass
    return payload


def _resolve_ref(conn, home: Path, ref: str) -> Optional[str]:
    if not ref:
        return None
    if ref.startswith("env:"):
        value = os.environ.get(ref[len("env:"):])
        if not value:
            raise DaythreadError("environment variable {} is not set".format(ref[len("env:"):]))
        return value
    if ref.startswith("secret:"):
        return SecretVault(conn, home).get(ref[len("secret:"):])
    return ref
