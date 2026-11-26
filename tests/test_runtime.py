import tempfile
import unittest
from pathlib import Path

from daythread import policy, runtime
from daythread.db import connect, init_db


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_write_is_auto_and_undoable(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "launch.md", "content": "three directions"},
            intent="capture",
        )
        self.assertEqual(run["state"], "completed")
        note = self.home / "notes" / "launch.md"
        self.assertTrue(note.exists())
        self.assertIn("three directions", note.read_text(encoding="utf-8"))

        undone = runtime.undo_run(self.conn, self.home, run["id"])
        self.assertEqual(undone["state"], "undone")
        self.assertFalse(note.exists())

    def test_external_requires_approval(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "message.send",
            {"to": "maya@example.com", "subject": "Deck", "body": "hi"},
            intent="send",
        )
        self.assertEqual(run["state"], "pending_approval")
        sent = self.home / "outbox" / "sent.jsonl"
        self.assertFalse(sent.exists())

        approved = runtime.approve(self.conn, self.home, run["id"])
        self.assertEqual(approved["state"], "completed")
        self.assertTrue(sent.exists())
        self.assertIn("maya@example.com", sent.read_text(encoding="utf-8"))

    def test_irreversible_cannot_be_undone(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "message.send",
            {"to": "maya@example.com", "subject": "Deck", "body": "hi"},
        )
        runtime.approve(self.conn, self.home, run["id"])
        with self.assertRaises(ValueError):
            runtime.undo_run(self.conn, self.home, run["id"])

    def test_policy_deny_blocks_execution(self):
        policy.set_policy(self.conn, "external", "deny")
        run = runtime.propose(
            self.conn,
            self.home,
            "message.send",
            {"to": "maya@example.com", "subject": "Deck"},
        )
        self.assertEqual(run["state"], "denied")
        self.assertFalse((self.home / "outbox" / "sent.jsonl").exists())

    def test_task_create_and_undo(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "task.create",
            {"title": "Send investor update"},
        )
        self.assertEqual(run["state"], "completed")
        rows = self.conn.execute("SELECT * FROM tasks").fetchall()
        self.assertEqual(len(rows), 1)
        runtime.undo_run(self.conn, self.home, run["id"])
        rows = self.conn.execute("SELECT * FROM tasks").fetchall()
        self.assertEqual(len(rows), 0)

    def test_reject(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "message.send",
            {"to": "maya@example.com", "subject": "Deck"},
        )
        rejected = runtime.reject(self.conn, run["id"])
        self.assertEqual(rejected["state"], "rejected")

    def test_path_escape_is_rejected(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "../escape.md", "content": "nope"},
        )
        self.assertEqual(run["state"], "failed")


if __name__ == "__main__":
    unittest.main()
