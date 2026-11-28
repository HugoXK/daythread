import ipaddress
import json
import ssl
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from daythread import crypto
from daythread.db import connect, init_db, set_setting
from daythread.server import ApiServer


class ServerV3Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.db_path = self.home / "db.sqlite"
        conn = connect(self.db_path)
        init_db(conn)
        conn.close()
        self.server = ApiServer(self.home, self.db_path, port=0).start()
        self.token = self.server.token

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, auth=True, headers=None, raw=None):
        request_headers = dict(headers or {})
        if auth:
            request_headers["Authorization"] = "Bearer {}".format(self.token)
        data = None
        if raw is not None:
            data = raw
            request_headers["Content-Type"] = "application/json"
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        url = "http://127.0.0.1:{}{}".format(self.server.port, path)
        request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, json.loads(body) if body else {}, dict(exc.headers)

    def test_openapi_and_version(self):
        status, body, _ = self.request("GET", "/v1/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(body["openapi"], "3.1.0")
        status, body, _ = self.request("GET", "/v1/version")
        self.assertEqual(status, 200)
        self.assertIn("version", body)

    def test_request_id_is_echoed(self):
        status, body, headers = self.request(
            "GET", "/v1/version", headers={"X-Request-Id": "abc123"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Request-Id"), "abc123")

    def test_pagination(self):
        for index in range(3):
            self.request(
                "POST",
                "/v1/facts",
                {
                    "subject_type": "person",
                    "subject_id": "p{}".format(index),
                    "predicate": "role",
                    "value": "x",
                },
            )
        status, first, _ = self.request("GET", "/v1/facts?limit=2")
        self.assertEqual(len(first), 2)
        status, second, _ = self.request("GET", "/v1/facts?limit=2&offset=2")
        self.assertEqual(len(second), 1)

    def test_rate_limit(self):
        conn = connect(self.db_path)
        set_setting(conn, "api.rate_limit_per_minute", "2")
        conn.close()
        self.assertEqual(self.request("GET", "/v1/facts")[0], 200)
        self.assertEqual(self.request("GET", "/v1/facts")[0], 200)
        status, body, _ = self.request("GET", "/v1/facts")
        self.assertEqual(status, 429)
        self.assertEqual(body["error"], "rate_limited")

    def test_tick_and_notifications(self):
        status, result, _ = self.request("POST", "/v1/tick", {"job": "morning"})
        self.assertEqual(status, 200)
        self.assertIn(result["job"], ("morning",))
        status, notifications, _ = self.request("GET", "/v1/notifications")
        self.assertEqual(status, 200)
        self.assertEqual(len(notifications), 1)

    def test_untrusted_run_requires_approval(self):
        status, run, _ = self.request(
            "POST",
            "/v1/runs",
            {
                "steps": [{"id": "s1", "tool": "note.write", "args": {"path": "a.md", "content": "A"}}],
                "taint": "untrusted",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(run["state"], "pending_approval")
        self.assertEqual(run["taint"], "untrusted")

    def test_ask_echo(self):
        status, body, _ = self.request("POST", "/v1/ask", {"text": "hello"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "echo: hello")
        self.assertFalse(body["cached"])

    def request_raw(self, method, path, headers=None):
        request = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(self.server.port, path),
            method=method,
            headers=headers or {},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_invalid_host(self):
        status, body, _ = self.request(
            "GET",
            "/v1/version",
            headers={"Host": "evil.example"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_host")

    def test_prometheus_metrics(self):
        status, body, headers = self.request_raw(
            "GET", "/metrics", {"Authorization": "Bearer {}".format(self.token)}
        )
        self.assertEqual(status, 200)
        self.assertIn(b"daythread_", body)
        self.assertIn("text/plain", headers.get("Content-Type", ""))

    def test_rate_limit_persists_across_servers(self):
        conn = connect(self.db_path)
        set_setting(conn, "api.rate_limit_per_minute", "1")
        conn.close()

        # The server buckets limits by wall-clock minute. Pin the clock so the
        # two requests below cannot straddle a minute boundary, which would
        # silently move the second request into a fresh bucket.
        class FrozenClock:
            def time(self):
                return 1800000000.0

        with mock.patch("daythread.server.time", FrozenClock()):
            self.assertEqual(self.request("GET", "/v1/version")[0], 200)
            second = ApiServer(self.home, self.db_path, port=0).start()
            try:
                request = urllib.request.Request(
                    "http://127.0.0.1:{}/v1/version".format(second.port),
                    headers={"Authorization": "Bearer {}".format(second.token)},
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(ctx.exception.code, 429)
            finally:
                second.stop()

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_https_server(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        key_path = self.home / "key.pem"
        cert_path = self.home / "cert.pem"
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        server = ApiServer(
            self.home,
            self.db_path,
            port=0,
            certfile=str(cert_path),
            keyfile=str(key_path),
        ).start()
        try:
            self.assertEqual(server.scheme, "https")
            context = ssl._create_unverified_context()
            url = "https://127.0.0.1:{}/v1/health".format(server.port)
            with urllib.request.urlopen(url, context=context, timeout=5) as response:
                self.assertEqual(response.status, 200)
        finally:
            server.stop()

    def test_ui_page(self):
        status, body, headers = self.request_raw("GET", "/ui")
        self.assertEqual(status, 200)
        self.assertIn(b"Daythread", body)
        self.assertIn("text/html", headers.get("Content-Type", ""))

    def test_models(self):
        status, body, _ = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(body["config"]["provider"], "local.echo")


if __name__ == "__main__":
    unittest.main()
