import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daythread import connectors, crypto, oauth
from daythread.db import connect, init_db, set_setting

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


class FeishuOpener:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/calendars/primary"):
            return json.dumps({"data": {"calendar": {"calendar_id": "cal-1"}}}).encode("utf-8")
        if "/calendar/v4/calendars/" in url and "/events" in url:
            if request.get_method() == "POST":
                return json.dumps(
                    {"data": {"event": {"event_id": "new-event"}}}
                ).encode("utf-8")
            return json.dumps(
                {
                    "data": {
                        "items": [
                            {
                                "event_id": "e1",
                                "summary": "Team sync",
                                "start_time": "1795000000",
                                "end_time": "1795003600",
                            }
                        ]
                    }
                }
            ).encode("utf-8")
        if "/task/v2/tasks" in url:
            if request.get_method() == "POST":
                return json.dumps({"data": {"task": {"guid": "new-task"}}}).encode("utf-8")
            return json.dumps(
                {
                    "data": {
                        "items": [
                            {
                                "guid": "t1",
                                "summary": "Ship it",
                                "completed_at": None,
                                "due": {"timestamp": "1795000000"},
                            }
                        ]
                    }
                }
            ).encode("utf-8")
        return json.dumps({}).encode("utf-8")


class FeishuConnectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_calendar_sync(self):
        opener = FeishuOpener()
        items = connectors._feishu_calendar_sync("https://open.feishu.cn")(
            NOW, {"access_token": "x"}, opener
        )
        self.assertEqual(items[0]["payload"]["title"], "Team sync")
        self.assertEqual(items[0]["external_id"], "e1")

    def test_task_sync(self):
        opener = FeishuOpener()
        items = connectors._feishu_task_sync("https://open.feishu.cn")(
            NOW, {"access_token": "x"}, opener
        )
        self.assertEqual(items[0]["kind"], "task")
        self.assertEqual(items[0]["payload"]["title"], "Ship it")
        self.assertEqual(items[0]["payload"]["status"], "open")

    def test_calendar_create_action(self):
        opener = FeishuOpener()
        result = connectors._feishu_calendar_create("https://open.feishu.cn")(
            None,
            None,
            {"access_token": "x"},
            {"title": "Review", "start": "2026-11-26T10:00:00Z"},
            opener,
        )
        self.assertEqual(result["data"]["event"]["event_id"], "new-event")
        request = opener.requests[-1]
        self.assertEqual(request.get_method(), "POST")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["summary"], "Review")
        self.assertIn("timestamp", body["start_time"])

    def test_task_create_action(self):
        opener = FeishuOpener()
        result = connectors._feishu_task_create("https://open.feishu.cn")(
            None,
            None,
            {"access_token": "x"},
            {"title": "Write docs", "due": "2026-11-27T10:00:00Z"},
            opener,
        )
        self.assertEqual(result["data"]["task"]["guid"], "new-task")

    def test_oauth_config(self):
        set_setting(self.conn, "oauth.feishu.client_id", "cli_x")
        config = oauth.get_oauth_config(self.conn, "feishu", ["calendar:calendar"], self.home)
        self.assertIn("accounts.feishu.cn", config.auth_url)
        self.assertIn("open.feishu.cn", config.token_url)

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_task_sync_inserts_local_tasks(self):
        self.conn.execute(
            "INSERT INTO connectors(id, version, status, scopes, connected_at) VALUES('feishu.tasks', '1.0.0', 'connected', '[]', '2026-01-01')"
        )
        self.conn.commit()
        oauth.TokenStore(self.conn, self.home).save(
            "feishu.tasks",
            {
                "access_token": "x",
                "refresh_token": "r",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "scope": "task:task:read task:task:write",
            },
        )
        opener = FeishuOpener()
        result = connectors.sync(
            self.conn, "feishu.tasks", now=NOW, home=self.home, opener=opener
        )
        self.assertEqual(result["inserted"], 1)
        row = self.conn.execute("SELECT * FROM tasks").fetchone()
        self.assertEqual(row["title"], "Ship it")

    def test_registry_contains_feishu_and_lark(self):
        ids = {item["id"] for item in connectors.list_connectors(self.conn)}
        self.assertIn("feishu.calendar", ids)
        self.assertIn("feishu.tasks", ids)
        self.assertIn("lark.calendar", ids)
        self.assertIn("lark.tasks", ids)


if __name__ == "__main__":
    unittest.main()
