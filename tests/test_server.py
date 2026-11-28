import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from daythread import receipt
from daythread.db import connect, init_db
from daythread.server import ApiServer


class ServerTest(unittest.TestCase):
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

    def request(self, method, path, payload=None, auth=True, raw=None):
        headers = {}
        if auth:
            headers["Authorization"] = "Bearer {}".format(self.token)
        data = None
        if raw is not None:
            data = raw
            headers["Content-Type"] = "application/json"
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = "http://127.0.0.1:{}{}".format(self.server.port, path)
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, json.loads(body) if body else {}

    def request_full(self, method, path, payload=None, auth=True, headers=None):
        request_headers = dict(headers or {})
        if auth:
            request_headers["Authorization"] = "Bearer {}".format(self.token)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(self.server.port, path),
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return (
                    response.status,
                    json.loads(response.read()),
                    dict(response.headers),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), dict(exc.headers)

    def completed_run(self):
        status, run = self.request(
            "POST",
            "/v1/runs",
            {
                "steps": [
                    {
                        "id": "s1",
                        "tool": "note.write",
                        "args": {"path": "receipt.md", "content": "hello"},
                    }
                ]
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(run["state"], "completed")
        return run

    def test_health_without_auth(self):
        status, body = self.request("GET", "/v1/health", auth=False)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_unauthorized(self):
        status, body = self.request("GET", "/v1/facts", auth=False)
        self.assertEqual(status, 401)

    def test_fact_crud(self):
        status, fact = self.request(
            "POST",
            "/v1/facts",
            {"subject_type": "person", "subject_id": "maya", "predicate": "role", "value": "co-founder"},
        )
        self.assertEqual(status, 201)
        status, facts = self.request("GET", "/v1/facts")
        self.assertEqual(status, 200)
        self.assertEqual(len(facts), 1)
        status, _ = self.request("DELETE", "/v1/facts/{}".format(fact["id"]))
        self.assertEqual(status, 200)

    def test_run_approval_flow(self):
        status, run = self.request(
            "POST",
            "/v1/runs",
            {"steps": [{"id": "s1", "tool": "note.write", "args": {"path": "a.md", "content": "A"}}]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(run["state"], "completed")

        status, run = self.request(
            "POST",
            "/v1/runs",
            {"steps": [{"id": "s1", "tool": "message.send", "args": {"to": "maya@example.com"}}]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(run["state"], "pending_approval")
        status, approved = self.request("POST", "/v1/runs/{}/approve".format(run["id"]))
        self.assertEqual(status, 200)
        self.assertEqual(approved["state"], "completed")

    def test_invalid_json(self):
        status, body = self.request("POST", "/v1/facts", raw=b"not-json")
        self.assertEqual(status, 400)

    def test_metrics(self):
        status, metrics = self.request("GET", "/v1/metrics")
        self.assertEqual(status, 200)
        self.assertIsInstance(metrics, dict)

    def test_receipt_endpoint_requires_auth(self):
        run = self.completed_run()
        status, body, _ = self.request_full(
            "GET", "/v1/runs/{}/receipt".format(run["id"]), auth=False
        )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")
        self.assertNotIn("receipt", body)

    def test_receipt_endpoint_rejects_non_loopback_host(self):
        run = self.completed_run()
        status, body, _ = self.request_full(
            "GET",
            "/v1/runs/{}/receipt".format(run["id"]),
            headers={"Host": "evil.example"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_host")
        self.assertNotIn("receipt", body)

    def test_receipt_endpoint_unknown_run_is_404_without_internals(self):
        status, body = self.request("GET", "/v1/runs/does-not-exist/receipt")
        self.assertEqual(status, 404)
        text = json.dumps(body)
        self.assertNotIn(str(self.home), text)
        self.assertNotIn("Traceback", text)

    def test_receipt_endpoint_returns_cli_json_and_verified(self):
        run = self.completed_run()
        status, body, headers = self.request_full(
            "GET",
            "/v1/runs/{}/receipt".format(run["id"]),
            headers={"X-Request-Id": "req-receipt-1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Request-Id"), "req-receipt-1")
        self.assertIs(body["verified"], True)
        conn = connect(self.db_path)
        try:
            expected = json.loads(
                receipt.to_json(receipt.build_receipt(conn, run["id"]))
            )
        finally:
            conn.close()
        self.assertEqual(body["receipt"], expected)
        self.assertTrue(receipt.verify_receipt(body["receipt"])["ok"])

    def test_receipt_endpoint_marks_tampered_ledger_unverified(self):
        run = self.completed_run()
        conn = connect(self.db_path)
        conn.execute(
            "UPDATE run_steps SET payload = ? WHERE run_id = ? AND kind = 'executed'",
            ('{"tampered":true}', run["id"]),
        )
        conn.commit()
        conn.close()
        status, body = self.request("GET", "/v1/runs/{}/receipt".format(run["id"]))
        self.assertEqual(status, 200)
        self.assertIs(body["verified"], False)


if __name__ == "__main__":
    unittest.main()
