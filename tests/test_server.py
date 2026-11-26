import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
