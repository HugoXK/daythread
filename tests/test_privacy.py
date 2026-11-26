import tempfile
import unittest
from pathlib import Path

from daythread import memory, privacy
from daythread.db import connect, init_db


class PrivacyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        self.fact = memory.add_fact(self.conn, "person", "maya", "role", "co-founder")
        memory.edit_fact(self.conn, self.fact["id"], value="co-founder & CTO")
        secret = memory.add_fact(self.conn, "person", "maya", "token", "secret-value")
        memory.edit_fact(self.conn, secret["id"], sensitivity="secret")
        memory.add_fact(self.conn, "person", "hugo", "role", "engineer")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_export_subject_includes_sensitive_and_history(self):
        data = privacy.export_subject(self.conn, "maya")
        self.assertEqual(len(data["facts"]), 2)
        self.assertEqual(len(data["history"]), 4)
        self.assertIn("secret-value", str(data))

    def test_soft_forget_tombstones(self):
        counts = privacy.forget_subject(self.conn, "maya")
        self.assertEqual(counts["facts"], 2)
        remaining = memory.list_facts(self.conn)
        self.assertEqual([fact["subject_id"] for fact in remaining], ["hugo"])
        self.assertIsNotNone(memory.get_fact(self.conn, self.fact["id"])["deleted_at"])

    def test_hard_forget_removes_rows_and_events(self):
        counts = privacy.forget_subject(self.conn, "maya", hard=True)
        self.assertGreaterEqual(counts["facts"], 2)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM facts WHERE subject_id = 'maya'"
            ).fetchone()[0],
            0,
        )
        self.assertGreater(counts["events"], 0)
        remaining = memory.list_facts(self.conn)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["subject_id"], "hugo")

    def test_purge_tombstones(self):
        memory.delete_fact(self.conn, self.fact["id"])
        self.conn.execute(
            "UPDATE facts SET deleted_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (self.fact["id"],),
        )
        self.conn.commit()
        counts = privacy.purge_tombstones(self.conn, older_than_days=0)
        self.assertEqual(counts["facts"], 1)
        with self.assertRaises(KeyError):
            memory.get_fact(self.conn, self.fact["id"])


if __name__ == "__main__":
    unittest.main()
