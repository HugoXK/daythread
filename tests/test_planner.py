import tempfile
import unittest
from pathlib import Path

from daythread import planner
from daythread.db import connect, init_db


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_toposort(self):
        steps = [
            {"id": "s2", "tool": "note.write", "args": {}, "depends_on": ["s1"]},
            {"id": "s1", "tool": "note.write", "args": {}},
        ]
        ordered = planner.validate(steps)
        self.assertEqual([step["id"] for step in ordered], ["s1", "s2"])

    def test_cycle_rejected(self):
        steps = [
            {"id": "a", "tool": "note.write", "args": {}, "depends_on": ["b"]},
            {"id": "b", "tool": "note.write", "args": {}, "depends_on": ["a"]},
        ]
        with self.assertRaises(ValueError):
            planner.validate(steps)

    def test_unknown_tool_rejected(self):
        with self.assertRaises(KeyError):
            planner.validate([{"id": "s1", "tool": "nope", "args": {}}])

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):
            planner.validate(
                [
                    {"id": "s1", "tool": "note.write", "args": {}},
                    {"id": "s1", "tool": "note.write", "args": {}},
                ]
            )

    def test_send_intent(self):
        steps = planner.from_intent("send maya@example.com | Deck | Latest")
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["tool"], "message.draft")
        self.assertEqual(steps[1]["tool"], "message.send")
        self.assertEqual(steps[1]["depends_on"], ["s1"])

    def test_preview_requires_approval(self):
        steps = planner.from_intent("send maya@example.com | Deck | Latest")
        preview = planner.preview(self.conn, steps)
        self.assertTrue(preview["requires_approval"])
        self.assertEqual(preview["risk"], "external")
        self.assertEqual([item["id"] for item in preview["steps"]], ["s1", "s2"])


if __name__ == "__main__":
    unittest.main()
