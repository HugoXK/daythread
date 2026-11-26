import tempfile
import unittest
from pathlib import Path

from daythread import runtime
from daythread.db import connect, init_db
from daythread.ledger import get_steps, verify_run


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_chain_verifies(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "launch.md", "content": "hello"},
            intent="test",
        )
        self.assertEqual(run["state"], "completed")
        ok, errors = verify_run(self.conn, run["id"])
        self.assertTrue(ok, errors)
        self.assertGreaterEqual(len(get_steps(self.conn, run["id"])), 4)

    def test_tamper_detected(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "launch.md", "content": "hello"},
            intent="test",
        )
        self.conn.execute(
            "UPDATE run_steps SET payload = ? WHERE run_id = ? AND kind = 'executed'",
            ('{"tampered":true}', run["id"]),
        )
        self.conn.commit()
        ok, errors = verify_run(self.conn, run["id"])
        self.assertFalse(ok)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
