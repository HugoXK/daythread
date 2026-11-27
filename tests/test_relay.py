import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from daythread import crypto, memory, relay, sync
from daythread.db import connect, init_db
from daythread.errors import ProviderError


@unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home_a = self.root / "a"
        self.home_b = self.root / "b"
        self.conn_a = connect(self.home_a / "db.sqlite")
        init_db(self.conn_a)
        self.conn_b = connect(self.home_b / "db.sqlite")
        init_db(self.conn_b)
        memory.add_fact(self.conn_a, "person", "maya", "role", "co-founder")
        self.server = relay.RelayServer(
            self.root / "relay", token="relay-token", port=0
        ).start()

    def tearDown(self):
        self.server.stop()
        self.conn_a.close()
        self.conn_b.close()
        self.tmp.cleanup()

    def test_push_pull_roundtrip(self):
        pushed = sync.push(
            self.conn_a,
            self.home_a,
            "correct horse",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        self.assertEqual(pushed["device_id"], "device-a")
        remote = sync.list_remote(self.server.url, "relay-token")
        self.assertEqual(remote["bundles"][0]["device_id"], "device-a")

        counts = sync.pull(
            self.conn_b,
            self.home_b,
            "correct horse",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        self.assertEqual(counts["facts"], 1)
        self.assertEqual(len(memory.list_facts(self.conn_b)), 1)

        raw = (self.root / "relay" / "device-a.json").read_text(encoding="utf-8")
        self.assertNotIn("co-founder", raw)

    def test_conflict_pull_and_retry(self):
        memory.add_fact(self.conn_b, "person", "hugo", "role", "engineer")
        sync.push(
            self.conn_a,
            self.home_a,
            "pw",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        with self.assertRaises(ProviderError):
            sync.push(
                self.conn_b,
                self.home_b,
                "pw",
                self.server.url,
                "relay-token",
                remote_device="device-a",
                if_match="wrong",
            )
        sync.push(
            self.conn_b,
            self.home_b,
            "pw",
            self.server.url,
            "relay-token",
            remote_device="device-a",
            on_conflict="pull",
        )
        sync.pull(
            self.conn_a,
            self.home_a,
            "pw",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        subjects = {fact["subject_id"] for fact in memory.list_facts(self.conn_a)}
        self.assertEqual(subjects, {"maya", "hugo"})

    def test_unauthorized(self):
        with self.assertRaises(ProviderError):
            sync.push(
                self.conn_a,
                self.home_a,
                "pw",
                self.server.url,
                "wrong-token",
                remote_device="device-a",
            )

    def _request(self, method, path, token):
        request = urllib.request.Request(
            self.server.url + path,
            method=method,
            headers={"Authorization": "Bearer {}".format(token)},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, {}

    def _request_full(self, method, path, token, data=None, headers=None):
        request_headers = {"Authorization": "Bearer {}".format(token)}
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.server.url + path,
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, json.loads(body) if body else {}, dict(exc.headers)

    def test_etag_metadata_and_conflict(self):
        bundle = sync.build_bundle(self.conn_a, "pw")
        data = json.dumps(bundle).encode("utf-8")
        status, body, headers = self._request_full(
            "PUT",
            "/v1/bundles/device-a",
            "relay-token",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        etag = headers.get("ETag")
        self.assertTrue(etag)

        status, meta, _ = self._request_full(
            "GET", "/v1/bundles/device-a/meta", "relay-token"
        )
        self.assertEqual(status, 200)
        self.assertEqual(meta["sha256"], body["sha256"])

        status, _, _ = self._request_full(
            "PUT",
            "/v1/bundles/device-a",
            "relay-token",
            data=data,
            headers={"Content-Type": "application/json", "If-Match": '"wrong"'},
        )
        self.assertEqual(status, 409)

        status, _, _ = self._request_full(
            "PUT",
            "/v1/bundles/device-a",
            "relay-token",
            data=data,
            headers={"Content-Type": "application/json", "If-Match": etag},
        )
        self.assertEqual(status, 200)

    def test_expired_device_token(self):
        self.server.device_tokens["device-a"] = {
            "token": "device-token",
            "expires_at": "2000-01-01T00:00:00+00:00",
        }
        self.assertEqual(
            self._request("GET", "/v1/bundles/device-a", "device-token")[0], 401
        )

    def test_delete(self):
        sync.push(
            self.conn_a,
            self.home_a,
            "pw",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        status, body = self._request("DELETE", "/v1/bundles/device-a", "relay-token")
        self.assertEqual(status, 200)
        self.assertTrue(body["deleted"])
        with self.assertRaises(ProviderError):
            sync.pull(
                self.conn_b,
                self.home_b,
                "pw",
                self.server.url,
                "relay-token",
                remote_device="device-a",
            )

    def test_retention_cleanup(self):
        sync.push(
            self.conn_a,
            self.home_a,
            "pw",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        path = self.root / "relay" / "device-a.json"
        old = time.time() - 10 * 86400
        os.utime(path, (old, old))
        self.server.retention_days = 7
        self.assertEqual(self.server.cleanup(), 1)
        self.assertFalse(path.exists())

    def test_device_tokens(self):
        self.server.device_tokens["device-a"] = "device-token"
        sync.push(
            self.conn_a,
            self.home_a,
            "pw",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        self.assertEqual(
            self._request("GET", "/v1/bundles/device-a", "device-token")[0], 200
        )
        self.assertEqual(
            self._request("GET", "/v1/bundles/device-a", "wrong")[0], 401
        )
        self.assertEqual(self._request("GET", "/v1/bundles", "device-token")[0], 401)
        self.assertEqual(self._request("GET", "/v1/bundles", "relay-token")[0], 200)

    def test_missing_bundle(self):
        with self.assertRaises(ProviderError):
            sync.pull(
                self.conn_b,
                self.home_b,
                "pw",
                self.server.url,
                "relay-token",
                remote_device="missing",
            )


if __name__ == "__main__":
    unittest.main()
