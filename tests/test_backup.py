import json
import tempfile
import unittest
from pathlib import Path

from daythread import backup, connectors, mcp, memory, policy, skills
from daythread.db import connect, init_db, set_setting
from daythread.errors import BackupError


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self):
        memory.add_fact(self.conn, "person", "maya", "role", "co-founder")
        self.conn.execute(
            "INSERT INTO tasks(id, title, status, created_at, updated_at) VALUES('t1', 'Ship it', 'open', '2026-01-01', '2026-01-01')"
        )
        self.conn.commit()
        connectors.connect(self.conn, "mock.gmail", token_ref="secret-ref")
        skills.install(
            self.conn,
            {
                "id": "local.note",
                "version": "0.1.0",
                "steps": [{"id": "s1", "tool": "note.write", "args": {"path": "x.md", "content": "x"}}],
            },
        )
        policy.set_policy(self.conn, "external", "deny")
        set_setting(self.conn, "model.api_key_ref", "SHOULD_NOT_APPEAR")
        from daythread import runtime

        runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "a.md", "content": "A"},
            intent="backup run",
        )

    def test_create_verify_and_restore(self):
        self._seed()
        path = backup.create_backup(self.conn, self.home)
        self.assertTrue(path.exists())
        info = backup.verify_backup(path, home=self.home)
        self.assertEqual(info["counts"]["facts"], 1)

        new_home = Path(self.tmp.name) / "restored"
        new_conn = connect(new_home / "db.sqlite")
        init_db(new_conn)
        counts = backup.restore_backup(new_conn, path, home=self.home)
        self.assertEqual(counts["facts"], 1)
        self.assertEqual(len(memory.list_facts(new_conn)), 1)
        self.assertEqual(new_conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        self.assertEqual(skills.get_skill(new_conn, "local.note")["id"], "local.note")
        self.assertEqual(policy.decide(new_conn, "external"), "deny")
        self.assertEqual(new_conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        self.assertGreater(
            new_conn.execute("SELECT COUNT(*) FROM run_steps").fetchone()[0], 0
        )
        new_conn.close()

    def test_tamper_is_rejected(self):
        self._seed()
        path = backup.create_backup(self.conn, self.home)
        bundle = json.loads(path.read_text(encoding="utf-8"))
        bundle["data"]["counts"]["facts"] = 999
        path.write_text(json.dumps(bundle), encoding="utf-8")
        with self.assertRaises(BackupError):
            backup.load_backup(path, home=self.home)

    def test_secrets_are_redacted(self):
        self._seed()
        mcp.add_server(self.conn, "echo", "python3", ["server.py"], {"TOKEN": "literal"})
        path = backup.create_backup(self.conn, self.home)
        bundle = json.loads(path.read_text(encoding="utf-8"))
        connector = bundle["data"]["tables"]["connectors"][0]
        self.assertIsNone(connector["token_ref"])
        setting_keys = {row["key"] for row in bundle["data"]["tables"]["settings"]}
        self.assertNotIn("model.api_key_ref", setting_keys)
        server = bundle["data"]["tables"]["mcp_servers"][0]
        self.assertIn("[REDACTED]", server["env"])


if __name__ == "__main__":
    unittest.main()
