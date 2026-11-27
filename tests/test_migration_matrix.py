import tempfile
import unittest
from pathlib import Path

from daythread import db, migrations


class MigrationMatrixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = db.connect(self.home / "db.sqlite")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _v1(self):
        self.conn.executescript(db.SCHEMA)
        self.conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1')")
        self.conn.execute(
            """
            INSERT INTO facts(
                id, subject_type, subject_id, predicate, object, confidence,
                provenance, valid_from, valid_to, superseded_by, created_by,
                deleted_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "f1",
                "person",
                "maya",
                "role",
                '"co-founder"',
                0.8,
                "[]",
                "2026-01-01",
                None,
                None,
                "user",
                None,
                "2026-01-01",
            ),
        )
        self.conn.commit()

    def test_v1_to_latest(self):
        self._v1()
        self.assertEqual(migrations.migrate(self.conn), migrations.LATEST_VERSION)
        row = self.conn.execute("SELECT * FROM facts WHERE id='f1'").fetchone()
        self.assertEqual(row["subject_id"], "maya")
        columns = {item["name"] for item in self.conn.execute("PRAGMA table_info(runs)")}
        self.assertIn("lease_owner", columns)
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT name FROM sqlite_master WHERE name='api_rate'"
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT name FROM sqlite_master WHERE name='facts_fts'"
            ).fetchone()
        )

    def test_v4_to_latest_purges_plaintext_cache(self):
        self._v1()
        migrations._migrate_v2(self.conn)
        migrations._migrate_v3(self.conn)
        migrations._migrate_v4(self.conn)
        self.conn.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
        self.conn.execute(
            "INSERT INTO model_cache(key, provider, model, response, created_at, expires_at) VALUES('k','p','m','plaintext','2026-01-01','2099-01-01')"
        )
        self.conn.commit()
        self.assertEqual(migrations.migrate(self.conn), migrations.LATEST_VERSION)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM model_cache").fetchone()[0], 0
        )

    def test_idempotent(self):
        self._v1()
        self.assertEqual(migrations.migrate(self.conn), migrations.LATEST_VERSION)
        self.assertEqual(migrations.migrate(self.conn), migrations.LATEST_VERSION)

    def test_init_db_empty_database(self):
        db.init_db(self.conn)
        self.assertEqual(migrations.current_version(self.conn), migrations.LATEST_VERSION)


if __name__ == "__main__":
    unittest.main()
