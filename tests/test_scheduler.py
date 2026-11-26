import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daythread import scheduler
from daythread.db import connect, init_db

MORNING = datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc)
EVENING = datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc)
AFTERNOON = datetime(2026, 11, 26, 14, 0, tzinfo=timezone.utc)


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        self.conn.execute(
            "INSERT INTO tasks(id, title, status, due, created_at, updated_at) VALUES('t1', 'Ship it', 'open', ?, ?, ?)",
            (MORNING.date().isoformat(), MORNING.isoformat(), MORNING.isoformat()),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_morning_tick_delivers_and_is_idempotent(self):
        result = scheduler.tick(self.conn, self.home, "morning", now=MORNING)
        self.assertFalse(result["already_ran"])
        self.assertIn(result["status"], ("delivered", "queued"))
        notifications = scheduler.list_notifications(self.conn)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["kind"], "morning")
        if notifications[0]["status"] == "delivered":
            self.assertTrue((self.home / "outbox" / "notifications.jsonl").exists())

        again = scheduler.tick(self.conn, self.home, "morning", now=MORNING)
        self.assertTrue(again["already_ran"])
        self.assertEqual(len(scheduler.list_notifications(self.conn)), 1)

    def test_night_tick_is_queued(self):
        result = scheduler.tick(self.conn, self.home, "morning", now=NIGHT)
        self.assertFalse(result["already_ran"])
        notification = scheduler.list_notifications(self.conn)[0]
        self.assertEqual(notification["status"], "queued")
        self.assertFalse((self.home / "outbox" / "notifications.jsonl").exists())

    def test_evening_tick(self):
        result = scheduler.tick(self.conn, self.home, "evening", now=EVENING)
        self.assertEqual(result["job"], "evening")
        self.assertEqual(len(scheduler.list_notifications(self.conn)), 1)

    def test_maintenance(self):
        result = scheduler.tick(self.conn, self.home, "maintenance", now=NIGHT)
        self.assertEqual(result["status"], "ok")
        self.assertIn("decayed_facts", result["detail"])

    def test_run_due_windows(self):
        due = scheduler.run_due(self.conn, self.home, now=MORNING)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["job"], "morning")
        self.assertEqual(scheduler.run_due(self.conn, self.home, now=AFTERNOON), [])
        night = scheduler.run_due(self.conn, self.home, now=NIGHT)
        self.assertEqual(night[0]["job"], "maintenance")


if __name__ == "__main__":
    unittest.main()
