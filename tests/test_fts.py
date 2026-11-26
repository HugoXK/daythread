import tempfile
import unittest
from pathlib import Path

from daythread import memory
from daythread.db import connect, init_db


class FtsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_fts_table_exists(self):
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'facts_fts'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(memory._fts_available(self.conn))

    def test_search_tracks_updates_and_deletes(self):
        fact = memory.add_fact(self.conn, "person", "maya", "role", "engineer")
        self.assertEqual(len(memory.search_facts(self.conn, "engineer")), 1)
        memory.edit_fact(self.conn, fact["id"], value="co-founder")
        self.assertEqual(memory.search_facts(self.conn, "engineer"), [])
        self.assertEqual(len(memory.search_facts(self.conn, "co-founder")), 1)
        memory.delete_fact(self.conn, fact["id"])
        self.assertEqual(memory.search_facts(self.conn, "co-founder"), [])

    def test_search_handles_special_characters(self):
        memory.add_fact(self.conn, "person", "maya", "note", "a-b:c")
        self.assertIsInstance(memory.search_facts(self.conn, 'a"b'), list)
        self.assertEqual(len(memory.search_facts(self.conn, "a-b:c")), 1)

    def test_fallback_when_fts_is_missing(self):
        memory.add_fact(self.conn, "person", "maya", "role", "engineer")
        self.conn.executescript(
            """
            DROP TRIGGER IF EXISTS facts_ai;
            DROP TRIGGER IF EXISTS facts_ad;
            DROP TRIGGER IF EXISTS facts_au;
            DROP TABLE IF EXISTS facts_fts;
            """
        )
        self.conn.commit()
        self.assertFalse(memory._fts_available(self.conn))
        self.assertEqual(len(memory.search_facts(self.conn, "engineer")), 1)


if __name__ == "__main__":
    unittest.main()
