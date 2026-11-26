import tempfile
import unittest
from pathlib import Path

from daythread import skills
from daythread.db import connect, init_db


class SkillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_builtins(self):
        ids = {item["id"] for item in skills.list_skills(self.conn)}
        self.assertIn("morning.brief", ids)
        self.assertIn("weekly.review", ids)
        manifest = skills.get_skill(self.conn, "morning.brief")
        self.assertTrue(manifest["builtin"])
        self.assertEqual(manifest["permissions"], ["brief.read"])

    def test_install_and_run(self):
        manifest = {
            "id": "local.note",
            "version": "0.1.0",
            "name": "Local note",
            "steps": [{"id": "s1", "tool": "note.write", "args": {"path": "x.md", "content": "x"}}],
        }
        installed = skills.install(self.conn, manifest)
        self.assertEqual(installed["permissions"], ["notes.write"])
        steps = skills.run(self.conn, "local.note")
        self.assertEqual(steps[0]["tool"], "note.write")
        skills.uninstall(self.conn, "local.note")
        with self.assertRaises(KeyError):
            skills.get_skill(self.conn, "local.note")

    def test_cannot_uninstall_builtin(self):
        with self.assertRaises(ValueError):
            skills.uninstall(self.conn, "morning.brief")

    def test_invalid_manifest(self):
        with self.assertRaises(ValueError):
            skills.install(self.conn, {"id": "bad"})


if __name__ == "__main__":
    unittest.main()
