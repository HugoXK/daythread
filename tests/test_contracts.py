import tempfile
import unittest
from pathlib import Path

from daythread import backup, connectors, db, mcp_server, server, skills, sync
from daythread.cli import build_parser


class ContractTest(unittest.TestCase):
    def test_cli_commands(self):
        parser = build_parser()
        choices = parser._subparsers._group_actions[0].choices
        for command in (
            "init",
            "migrate",
            "doctor",
            "fact",
            "plan",
            "connector",
            "mcp",
            "sync",
            "relay",
            "privacy",
            "maintenance",
            "backup",
            "restore",
            "serve",
            "bench",
        ):
            self.assertIn(command, choices)

    def test_openapi_paths(self):
        paths = server._openapi()["paths"]
        for path in (
            "/v1/health",
            "/v1/facts",
            "/v1/runs",
            "/v1/runs/{id}/approve",
            "/v1/runs/{id}/undo",
            "/v1/connectors",
            "/v1/connectors/{id}/sync",
            "/v1/models",
            "/v1/ask",
            "/v1/metrics",
        ):
            self.assertIn(path, paths)

    def test_connector_manifests(self):
        for connector_id, spec in connectors.REGISTRY.items():
            self.assertEqual(spec.id, connector_id)
            self.assertTrue(spec.name)
            self.assertTrue(spec.version)
            self.assertIn(spec.auth, ("mock", "oauth2"))
            self.assertTrue(spec.scopes)
            self.assertTrue(spec.actions)
            if spec.auth == "oauth2":
                self.assertIn(spec.oauth_provider, ("google", "microsoft", "feishu", "lark"))
            action_names = {action["name"] for action in spec.actions}
            self.assertTrue(set(spec.action_handlers).issubset(action_names))

    def test_mcp_server_schemas(self):
        schemas = mcp_server._schemas()
        names = {schema["name"] for schema in schemas}
        self.assertIn("daythread.today", names)
        self.assertIn("daythread.propose_plan", names)
        for schema in schemas:
            self.assertIn("name", schema)
            self.assertIn("description", schema)
            self.assertIn("inputSchema", schema)

    def test_skill_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "db.sqlite")
            try:
                db.init_db(conn)
                for manifest in skills.list_skills(conn):
                    self.assertTrue(manifest["steps"])
                    self.assertTrue(manifest["permissions"])
            finally:
                conn.close()

    def test_backup_and_sync_formats_are_versioned(self):
        self.assertTrue(backup.FORMAT.endswith(".v1"))
        self.assertTrue(sync.FORMAT.endswith(".v1"))


if __name__ == "__main__":
    unittest.main()
