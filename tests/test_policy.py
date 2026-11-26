import tempfile
import unittest
from pathlib import Path

from daythread import policy
from daythread.db import connect, init_db


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_defaults(self):
        current = policy.load_policy(self.conn)
        self.assertEqual(current["read"], "allow")
        self.assertEqual(current["write"], "allow")
        self.assertEqual(current["external"], "ask")
        self.assertEqual(current["destructive"], "ask")

    def test_set_and_decide(self):
        policy.set_policy(self.conn, "external", "deny")
        self.assertEqual(policy.decide(self.conn, "external"), "deny")

    def test_invalid_risk(self):
        with self.assertRaises(ValueError):
            policy.set_policy(self.conn, "nope", "allow")

    def test_invalid_decision(self):
        with self.assertRaises(ValueError):
            policy.set_policy(self.conn, "write", "maybe")


if __name__ == "__main__":
    unittest.main()
