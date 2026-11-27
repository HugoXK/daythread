import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from daythread import crypto, proactivity, webhook
from daythread.db import connect, init_db, set_setting
from daythread.server import ApiServer


@unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
class WebhookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.db_path = self.home / "db.sqlite"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        webhook.set_secret(self.conn, self.home, "demo", "shared-secret")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_sign_and_verify(self):
        body = b'{"id":"evt-1"}'
        signed = webhook.sign("shared-secret", body)
        self.assertTrue(
            webhook.verify(
                "shared-secret", body, signed["timestamp"], signed["signature"]
            )
        )
        self.assertFalse(
            webhook.verify(
                "shared-secret", body + b"x", signed["timestamp"], signed["signature"]
            )
        )
        old = webhook.sign("shared-secret", body, timestamp=int(time.time()) - 3600)
        self.assertFalse(
            webhook.verify(
                "shared-secret", body, old["timestamp"], old["signature"], max_age=300
            )
        )

    def test_provider_modes(self):
        body = b'{"clientState":"shared-secret"}'
        self.assertTrue(
            webhook.verify_request(
                "shared-secret", body, {}, mode="microsoft"
            )
        )
        self.assertFalse(
            webhook.verify_request(
                "shared-secret", b'{"clientState":"wrong"}', {}, mode="microsoft"
            )
        )
        self.assertTrue(
            webhook.verify_request(
                "token", b"{}", {"X-Goog-Channel-Token": "token"}, mode="google"
            )
        )
        timestamp = str(int(time.time()))
        nonce = "abc"
        signature = __import__("hashlib").sha256(
            (timestamp + nonce + "shared-secret").encode("utf-8") + b"{}"
        ).hexdigest()
        self.assertTrue(
            webhook.verify_request(
                "shared-secret",
                b"{}",
                {
                    "X-Lark-Request-Timestamp": timestamp,
                    "X-Lark-Request-Nonce": nonce,
                    "X-Lark-Signature": signature,
                },
                mode="feishu",
            )
        )

    def test_ingest_dedupe(self):
        first = webhook.ingest(self.conn, "demo", {"id": "evt-1"}, dedupe_key="evt-1")
        second = webhook.ingest(self.conn, "demo", {"id": "evt-1"}, dedupe_key="evt-1")
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(webhook.list_events(self.conn)), 1)

    def _post(self, path, body, headers):
        request = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(self.server.port, path),
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, {}

    def test_server_microsoft_mode(self):
        set_setting(self.conn, "webhook.demo.mode", "microsoft")
        self.server = ApiServer(self.home, self.db_path, port=0).start()
        try:
            body = json.dumps(
                {
                    "clientState": "shared-secret",
                    "value": [{"id": "ms1", "title": "Deploy finished"}],
                }
            ).encode("utf-8")
            status, payload = self._post(
                "/v1/webhooks/demo",
                body,
                {"Content-Type": "application/json"},
            )
            self.assertEqual(status, 202)
            self.assertTrue(payload["signal_id"])
            self.assertEqual(
                self._post(
                    "/v1/webhooks/demo",
                    b'{"clientState":"wrong"}',
                    {"Content-Type": "application/json"},
                )[0],
                401,
            )
        finally:
            self.server.stop()

    def test_server_webhook(self):
        self.server = ApiServer(self.home, self.db_path, port=0).start()
        try:
            body = json.dumps({"id": "evt-1", "title": "Deploy finished"}).encode("utf-8")
            signed = webhook.sign("shared-secret", body)
            headers = {
                "Content-Type": "application/json",
                webhook.TIMESTAMP_HEADER: signed["timestamp"],
                webhook.SIGNATURE_HEADER: signed["signature"],
            }
            status, payload = self._post("/v1/webhooks/demo", body, headers)
            self.assertEqual(status, 202)
            self.assertFalse(payload["duplicate"])

            status, payload = self._post("/v1/webhooks/demo", body, headers)
            self.assertEqual(status, 202)
            self.assertTrue(payload["duplicate"])

            bad = dict(headers)
            bad[webhook.SIGNATURE_HEADER] = "sha256=deadbeef"
            self.assertEqual(self._post("/v1/webhooks/demo", body, bad)[0], 401)
            self.assertEqual(self._post("/v1/webhooks/missing", body, headers)[0], 401)

            old = webhook.sign("shared-secret", body, timestamp=int(time.time()) - 3600)
            stale = {
                "Content-Type": "application/json",
                webhook.TIMESTAMP_HEADER: old["timestamp"],
                webhook.SIGNATURE_HEADER: old["signature"],
            }
            self.assertEqual(self._post("/v1/webhooks/demo", body, stale)[0], 401)
        finally:
            self.server.stop()


class WebhookTaintTest(unittest.TestCase):
    """Webhook content is external input: it must land untrusted.

    This class deliberately does not require the cryptography extra, because
    the taint contract must be covered on every CI matrix entry.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.db_path = self.home / "db.sqlite"
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _taint(self, signal_id):
        row = self.conn.execute(
            "SELECT taint FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        return row["taint"]

    def test_ingest_marks_signals_untrusted(self):
        single = webhook.ingest(
            self.conn,
            "demo",
            {"id": "evt-1", "title": "Deploy finished"},
            dedupe_key="evt-1",
        )
        self.assertFalse(single["duplicate"])
        self.assertEqual(self._taint(single["signal_id"]), "untrusted")

        batch = webhook.ingest(
            self.conn,
            "demo",
            {"value": [{"id": "ms-1"}, {"id": "ms-2"}]},
            dedupe_key="evt-2",
        )
        self.assertEqual(len(batch["signal_ids"]), 2)
        for signal_id in batch["signal_ids"]:
            self.assertEqual(self._taint(signal_id), "untrusted")

    def test_internal_signal_default_stays_trusted(self):
        # Scope guard: only the webhook entry changes; internal signals keep
        # the trusted default until taint propagation is designed separately.
        signal = proactivity.ingest_signal(
            self.conn, "internal", "note", {"title": "local"}
        )
        self.assertEqual(signal["taint"], "trusted")


if __name__ == "__main__":
    unittest.main()
