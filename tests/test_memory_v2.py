import tempfile
import unittest
from pathlib import Path

from daythread import memory
from daythread.db import connect, init_db


class MemoryV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_history(self):
        fact = memory.add_fact(self.conn, "person", "maya", "role", "engineer")
        memory.edit_fact(self.conn, fact["id"], value="co-founder")
        memory.delete_fact(self.conn, fact["id"])
        actions = [entry["action"] for entry in memory.history(self.conn, fact["id"])]
        self.assertEqual(actions, ["add", "update", "delete"])

    def test_supersede(self):
        old = memory.add_fact(self.conn, "project", "daythread", "stage", "prototype")
        new = memory.supersede_fact(self.conn, old["id"], "baseline")
        active = memory.list_facts(self.conn)
        self.assertEqual([fact["id"] for fact in active], [new["id"]])
        old = memory.get_fact(self.conn, old["id"])
        self.assertEqual(old["superseded_by"], new["id"])
        self.assertIsNotNone(old["valid_to"])

    def test_conflicts(self):
        memory.add_fact(self.conn, "person", "maya", "timezone", "Asia/Shanghai")
        memory.add_fact(self.conn, "person", "maya", "timezone", "Europe/Berlin")
        conflicts = memory.conflicts(self.conn)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["variants"], 2)

    def test_extract_and_apply(self):
        text = "Maya is my co-founder. I prefer async updates. deadline 2026-12-01"
        candidates = memory.extract_facts(text)
        predicates = {(item["subject_type"], item["predicate"]) for item in candidates}
        self.assertIn(("person", "role"), predicates)
        self.assertIn(("self", "preference"), predicates)
        self.assertIn(("self", "deadline"), predicates)
        facts = memory.apply_extracted(self.conn, candidates)
        self.assertEqual(len(facts), len(candidates))
        self.assertTrue(memory.search_facts(self.conn, "co-founder"))

    def test_import_json(self):
        data = {
            "facts": [
                {
                    "subject_type": "person",
                    "subject_id": "maya",
                    "predicate": "role",
                    "object": "co-founder",
                    "confidence": 0.9,
                    "tags": ["team"],
                }
            ]
        }
        self.assertEqual(memory.import_json(self.conn, data), 1)
        facts = memory.list_facts(self.conn)
        self.assertEqual(facts[0]["tags"], ["team"])


if __name__ == "__main__":
    unittest.main()
