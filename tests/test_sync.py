import tempfile
import unittest
from pathlib import Path

from daythread import crypto, memory, runtime, sync
from daythread.db import connect, init_db
from daythread.errors import DaythreadError


@unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        memory.add_fact(self.conn, "person", "maya", "role", "co-founder")
        runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "a.md", "content": "A"},
            intent="sync test",
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_export_verify_import(self):
        path = sync.export_sync(self.conn, self.home, "correct horse")
        encoded = path.read_text(encoding="utf-8")
        self.assertNotIn("co-founder", encoded)

        info = sync.verify_sync(path, "correct horse")
        self.assertEqual(info["counts"]["facts"], 1)

        new_home = Path(self.tmp.name) / "new"
        new_conn = connect(new_home / "db.sqlite")
        init_db(new_conn)
        counts = sync.import_sync(new_conn, path, "correct horse")
        self.assertEqual(counts["facts"], 1)
        self.assertEqual(len(memory.list_facts(new_conn)), 1)
        self.assertEqual(new_conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        new_conn.close()

    def test_wrong_passphrase(self):
        path = sync.export_sync(self.conn, self.home, "correct")
        with self.assertRaises(DaythreadError):
            sync.decrypt_sync(path, "wrong")


if __name__ == "__main__":
    unittest.main()
