import json
import tempfile
import unittest
from pathlib import Path

from daythread import backup, crypto, export, memory
from daythread.db import connect, init_db
from daythread.errors import BackupError


class SensitivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        memory.add_fact(self.conn, "person", "maya", "role", "co-founder")
        secret = memory.add_fact(
            self.conn, "self", "self", "api_token", "secret-value"
        )
        memory.edit_fact(self.conn, secret["id"], sensitivity="secret")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_list_and_search_hide_sensitive_by_default(self):
        self.assertEqual(len(memory.list_facts(self.conn)), 1)
        self.assertEqual(len(memory.list_facts(self.conn, include_sensitive=True)), 2)
        self.assertEqual(memory.search_facts(self.conn, "secret-value"), [])
        self.assertEqual(
            len(memory.search_facts(self.conn, "secret-value", include_sensitive=True)),
            1,
        )

    def test_export_hides_sensitive_by_default(self):
        data = export.export_json(self.conn)
        self.assertEqual(len(data["facts"]), 1)
        self.assertNotIn("secret-value", json.dumps(data))
        full = export.export_json(self.conn, include_sensitive=True)
        self.assertEqual(len(full["facts"]), 2)
        markdown = export.export_markdown(self.conn)
        self.assertNotIn("secret-value", markdown)

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_backup_signature(self):
        path = backup.create_backup(self.conn, self.home)
        backup.load_backup(path, home=self.home)
        bundle = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("signature", bundle)

        bundle["signature"] = "0" * 64
        path.write_text(json.dumps(bundle), encoding="utf-8")
        with self.assertRaises(BackupError):
            backup.load_backup(path, home=self.home)
        backup.load_backup(path, home=self.home, verify_signature=False)

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_restore_to_new_home_requires_trust(self):
        path = backup.create_backup(self.conn, self.home)
        new_home = self.home / "new"
        new_conn = connect(new_home / "db.sqlite")
        init_db(new_conn)
        try:
            with self.assertRaises(BackupError):
                backup.restore_backup(new_conn, path, home=new_home)
            counts = backup.restore_backup(
                new_conn, path, home=new_home, verify_signature=False
            )
            self.assertEqual(counts["facts"], 2)
        finally:
            new_conn.close()


if __name__ == "__main__":
    unittest.main()
