import tempfile
import unittest
from pathlib import Path

from daythread import compliance
from daythread.db import connect, init_db, set_setting


class ComplianceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        set_setting(self.conn, "model.api_key_ref", "SHOULD_NOT_APPEAR")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_evidence(self):
        data = compliance.evidence(self.conn, self.home)
        self.assertEqual(data["latest_schema"], data["schema_version"])
        self.assertTrue(data["controls"])
        self.assertTrue(data["gaps"])
        self.assertNotIn("model.api_key_ref", data["settings"])

    def test_write_evidence(self):
        path = compliance.write_evidence(
            self.conn, self.home, self.home / "evidence.json"
        )
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
