import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daythread import brief, memory, runtime
from daythread.db import connect, init_db


class BriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_morning_brief_includes_local_state(self):
        runtime.propose(
            self.conn,
            self.home,
            "calendar.add",
            {"title": "Design review", "start": self.now.isoformat()},
        )
        runtime.propose(
            self.conn,
            self.home,
            "task.create",
            {"title": "Send investor update", "due": self.now.date().isoformat()},
        )
        memory.add_fact(
            self.conn, "self", "hugo", "commitment", "send the deck by Friday"
        )
        data = brief.morning_brief(self.conn, now=self.now)
        titles = [event["title"] for event in data["calendar"]]
        self.assertIn("Design review", titles)
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(len(data["commitments"]), 1)

        text = brief.render(data)
        self.assertIn("Morning brief", text)
        self.assertIn("Design review", text)
        self.assertIn("Send investor update", text)

    def test_evening_brief_uses_tomorrow(self):
        tomorrow = self.now + timedelta(days=1)
        runtime.propose(
            self.conn,
            self.home,
            "calendar.add",
            {"title": "Flight to Shanghai", "start": tomorrow.isoformat()},
        )
        data = brief.evening_brief(self.conn, now=self.now)
        titles = [event["title"] for event in data["calendar"]]
        self.assertIn("Flight to Shanghai", titles)
        self.assertIn("Evening brief", brief.render(data))


if __name__ == "__main__":
    unittest.main()
