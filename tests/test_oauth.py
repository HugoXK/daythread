import base64
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from daythread import crypto, oauth
from daythread.db import connect, init_db, set_setting
from daythread.errors import DaythreadError


class FakeTokenEndpoint:
    def __call__(self, request):
        return json.dumps(
            {
                "access_token": "access-123",
                "refresh_token": "refresh-123",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        ).encode("utf-8")


class OAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_pkce_pair(self):
        verifier, challenge = oauth.pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected)

    def test_authorization_url(self):
        config = oauth.OAuthConfig(
            provider="google",
            auth_url="https://example.invalid/auth",
            token_url="https://example.invalid/token",
            client_id="client",
            client_secret=None,
            scopes=["scope.a", "scope.b"],
        )
        url = oauth.build_authorization_url(
            config, "http://127.0.0.1:1/callback", "state", "challenge"
        )
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params["client_id"], ["client"])
        self.assertEqual(params["scope"], ["scope.a scope.b"])
        self.assertEqual(params["code_challenge_method"], ["S256"])

    def test_exchange_and_refresh(self):
        config = oauth.OAuthConfig(
            provider="google",
            auth_url="https://example.invalid/auth",
            token_url="https://example.invalid/token",
            client_id="client",
            client_secret="secret",
            scopes=["scope"],
        )
        token = oauth.exchange_code(
            config, "code", "verifier", "http://127.0.0.1/callback", FakeTokenEndpoint()
        )
        self.assertEqual(token["access_token"], "access-123")
        self.assertIn("expires_at", token)
        refreshed = oauth.refresh_token(config, token, FakeTokenEndpoint())
        self.assertEqual(refreshed["refresh_token"], "refresh-123")

    def test_expiry(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.assertTrue(oauth.is_expired({"expires_at": past}))
        self.assertFalse(oauth.is_expired({"expires_at": future}))
        self.assertFalse(oauth.is_expired({}))

    def test_microsoft_provider_config(self):
        set_setting(self.conn, "oauth.microsoft.client_id", "ms-client")
        config = oauth.get_oauth_config(self.conn, "microsoft", ["offline_access"], self.home)
        self.assertIn("login.microsoftonline.com", config.auth_url)
        self.assertIn("login.microsoftonline.com", config.token_url)

    def test_callback_server(self):
        server = oauth.CallbackServer(port=0).start()
        try:
            url = "{}?code=abc&state=xyz".format(server.redirect_uri)
            urllib.request.urlopen(url, timeout=5).read()
            result = server.wait_for_code(timeout=5)
        finally:
            server.stop()
        self.assertEqual(result["code"], "abc")
        self.assertEqual(result["state"], "xyz")

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_token_store(self):
        store = oauth.TokenStore(self.conn, self.home)
        store.save("google.gmail", {"access_token": "a", "refresh_token": "r"})
        self.assertEqual(store.load("google.gmail")["access_token"], "a")
        store.delete("google.gmail")
        self.assertIsNone(store.load("google.gmail"))

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_full_authorize_flow(self):
        set_setting(self.conn, "oauth.google.client_id", "client")
        set_setting(self.conn, "oauth.google.auth_url", "https://example.invalid/auth")
        set_setting(self.conn, "oauth.google.token_url", "https://example.invalid/token")

        captured = {}

        def on_url(url):
            params = parse_qs(urlparse(url).query)
            captured.update(params)
            callback = "{}?code=abc&state={}".format(
                params["redirect_uri"][0], params["state"][0]
            )
            urllib.request.urlopen(callback, timeout=5).read()

        token = oauth.authorize(
            self.conn,
            self.home,
            "google.calendar",
            open_browser=False,
            timeout=5,
            on_url=on_url,
            opener=FakeTokenEndpoint(),
        )
        self.assertEqual(token["access_token"], "access-123")
        self.assertIn("code_challenge", captured)
        store = oauth.TokenStore(self.conn, self.home)
        self.assertEqual(store.load("google.calendar")["access_token"], "access-123")
        row = self.conn.execute(
            "SELECT status FROM connectors WHERE id = 'google.calendar'"
        ).fetchone()
        self.assertEqual(row["status"], "connected")


if __name__ == "__main__":
    unittest.main()
