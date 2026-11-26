import tempfile
import unittest
from pathlib import Path

from daythread import migrations
from daythread.db import connect, init_db


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_latest_version(self):
        self.assertEqual(migrations.current_version(self.conn), migrations.LATEST_VERSION)

    def test_v2_columns(self):
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(facts)")}
        for expected in ("sensitivity", "tags", "metadata", "score", "supersedes"):
            self.assertIn(expected, columns)
        run_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(runs)")}
        for expected in ("plan", "budget", "checkpoint", "trace_id", "idempotency_key"):
            self.assertIn(expected, run_columns)

    def test_migrate_is_idempotent(self):
        self.assertEqual(migrations.migrate(self.conn), migrations.LATEST_VERSION)
        self.assertEqual(migrations.migrate(self.conn), migrations.LATEST_VERSION)

    def test_reopen_keeps_version(self):
        path = self.home / "db.sqlite"
        self.conn.close()
        self.conn = connect(path)
        self.assertEqual(migrations.current_version(self.conn), migrations.LATEST_VERSION)


if __name__ == "__main__":
    unittest.main()
