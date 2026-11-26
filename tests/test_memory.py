import tempfile
import unittest
from pathlib import Path

from daythread import memory
from daythread.db import connect, init_db


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_add_and_get(self):
        fact = memory.add_fact(self.conn, "person", "maya", "role", "co-founder")
        self.assertEqual(fact["subject_id"], "maya")
        self.assertEqual(fact["object"], "co-founder")
        self.assertEqual(fact["created_by"], "user")
        self.assertTrue(fact["provenance"])
        self.assertIsNone(fact["deleted_at"])

    def test_list_and_filter(self):
        memory.add_fact(self.conn, "person", "maya", "role", "co-founder")
        memory.add_fact(self.conn, "project", "daythread", "stage", "baseline")
        people = memory.list_facts(self.conn, subject_type="person")
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]["subject_id"], "maya")

    def test_edit_appends_provenance(self):
        fact = memory.add_fact(self.conn, "person", "maya", "role", "engineer")
        edited = memory.edit_fact(self.conn, fact["id"], value="co-founder")
        self.assertEqual(edited["object"], "co-founder")
        self.assertEqual(len(edited["provenance"]), 2)

    def test_delete_is_tombstone(self):
        fact = memory.add_fact(self.conn, "person", "maya", "role", "co-founder")
        memory.delete_fact(self.conn, fact["id"])
        self.assertEqual(memory.list_facts(self.conn), [])
        deleted = memory.get_fact(self.conn, fact["id"])
        self.assertIsNotNone(deleted["deleted_at"])

    def test_search_excludes_deleted(self):
        fact = memory.add_fact(self.conn, "person", "maya", "role", "co-founder")
        self.assertEqual(len(memory.search_facts(self.conn, "co-founder")), 1)
        memory.delete_fact(self.conn, fact["id"])
        self.assertEqual(memory.search_facts(self.conn, "co-founder"), [])

    def test_invalid_confidence(self):
        with self.assertRaises(ValueError):
            memory.add_fact(self.conn, "person", "maya", "role", "x", confidence=2.0)


if __name__ == "__main__":
    unittest.main()
