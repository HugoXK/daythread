import io
import json
import shlex
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from daythread.cli import main


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_add_list_and_brief(self):
        code, out, _ = run_cli(["--home", self.home, "init"])
        self.assertEqual(code, 0)
        self.assertIn("initialized", out)

        code, out, _ = run_cli(
            [
                "--home",
                self.home,
                "fact",
                "add",
                "--type",
                "person",
                "--subject",
                "maya",
                "--predicate",
                "role",
                "--value",
                "co-founder",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("remembered", out)

        code, out, _ = run_cli(["--home", self.home, "fact", "list"])
        self.assertEqual(code, 0)
        self.assertIn("maya", out)

        code, out, _ = run_cli(["--home", self.home, "--json", "brief"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["kind"], "morning")

    def test_act_approve_ledger_and_export(self):
        code, out, _ = run_cli(
            [
                "--home",
                self.home,
                "act",
                "note.write",
                "--arg",
                "path=launch.md",
                "--arg",
                "content=hello",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("completed", out)

        code, out, _ = run_cli(
            [
                "--home",
                self.home,
                "--json",
                "act",
                "message.send",
                "--arg",
                "to=maya@example.com",
                "--arg",
                "subject=Deck",
            ]
        )
        self.assertEqual(code, 0)
        run = json.loads(out)
        self.assertEqual(run["state"], "pending_approval")
        run_id = run["id"]

        code, out, _ = run_cli(["--home", self.home, "approve", run_id])
        self.assertEqual(code, 0)
        self.assertIn("completed", out)

        code, out, _ = run_cli(["--home", self.home, "ledger", run_id, "--verify"])
        self.assertEqual(code, 0)
        self.assertIn("ledger OK", out)

        code, out, _ = run_cli(
            ["--home", self.home, "--json", "export", "--format", "json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data["facts"]), 0)
        self.assertEqual(len(data["tasks"]), 0)
        self.assertEqual(len(data["runs"]), 2)

    def test_ledger_verify_exits_non_zero_on_tampered_chain(self):
        code, out, _ = run_cli(
            [
                "--home",
                self.home,
                "act",
                "note.write",
                "--arg",
                "path=launch.md",
                "--arg",
                "content=hello",
            ]
        )
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["--home", self.home, "--json", "runs", "--limit", "1"])
        run_id = json.loads(out)[0]["id"]

        # normal chain: text and --json paths both stay rc=0
        code, out, _ = run_cli(["--home", self.home, "ledger", run_id, "--verify"])
        self.assertEqual(code, 0)
        self.assertIn("ledger OK", out)
        code, out, _ = run_cli(
            ["--home", self.home, "--json", "ledger", run_id, "--verify"]
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

        db_path = Path(self.home) / "daythread.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE run_steps SET payload = ? WHERE run_id = ? AND seq = ("
            "SELECT MIN(seq) FROM run_steps WHERE run_id = ?)",
            ('{"tampered":true}', run_id, run_id),
        )
        conn.commit()
        conn.close()

        code, out, err = run_cli(["--home", self.home, "ledger", run_id, "--verify"])
        self.assertNotEqual(code, 0)
        self.assertIn("TAMPERED", out)
        self.assertIn("hash mismatch", out)
        self.assertIn("error: ledger verification failed:", err)
        self.assertNotIn("Traceback", err)

        code, out, err = run_cli(
            ["--home", self.home, "--json", "ledger", run_id, "--verify"]
        )
        self.assertNotEqual(code, 0)
        data = json.loads(out)
        self.assertFalse(data["ok"])
        self.assertTrue(data["errors"])
        self.assertIn("error:", err)
        self.assertNotIn("Traceback", err)

    def test_ledger_verify_rejects_unknown_run(self):
        code, out, err = run_cli(
            ["--home", self.home, "ledger", "deadbeef", "--verify"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error: run not found: deadbeef", err)
        self.assertNotIn("ledger OK", out)
        self.assertNotIn("Traceback", err)

        code, out, err = run_cli(
            ["--home", self.home, "--json", "ledger", "deadbeef", "--verify"]
        )
        self.assertEqual(code, 1)
        self.assertIn("run not found", err)
        self.assertNotIn("ledger OK", out)

    def test_ledger_verify_rejects_short_run_id_prefix(self):
        code, _, _ = run_cli(
            [
                "--home",
                self.home,
                "act",
                "note.write",
                "--arg",
                "path=launch.md",
                "--arg",
                "content=hello",
            ]
        )
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["--home", self.home, "--json", "runs", "--limit", "1"])
        run_id = json.loads(out)[0]["id"]
        code, out, err = run_cli(
            ["--home", self.home, "ledger", run_id[:8], "--verify"]
        )
        self.assertEqual(code, 1)
        self.assertNotIn("ledger OK", out)
        self.assertIn("run not found", err)

    def test_ledger_verify_empty_run_reports_no_steps(self):
        code, _, _ = run_cli(["--home", self.home, "init"])
        self.assertEqual(code, 0)
        conn = sqlite3.connect(str(Path(self.home) / "daythread.db"))
        conn.execute(
            "INSERT INTO runs(id, intent, tool, args, state, risk, decision, created_at, updated_at)"
            " VALUES('empty-run', 'x', 'note.write', '{}', 'completed', 'write', 'allow', ?, ?)",
            ("2026-11-28T00:00:00+00:00", "2026-11-28T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        code, out, err = run_cli(
            ["--home", self.home, "ledger", "empty-run", "--verify"]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("no steps for run empty-run", out)
        self.assertNotIn("ledger OK", out)
        self.assertNotIn("ledger empty-run:", out)

        code, out, err = run_cli(["--home", self.home, "ledger", "empty-run"])
        self.assertEqual(code, 0, err)
        self.assertIn("no steps for run empty-run", out)

        code, out, err = run_cli(
            ["--home", self.home, "--json", "ledger", "empty-run", "--verify"]
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(data["steps"], [])

    def test_serve_and_ui_reject_symlinked_home_before_side_effects(self):
        real = Path(self.home) / "real-home"
        real.mkdir()
        link = Path(self.home) / "linked home"
        link.symlink_to(real, target_is_directory=True)

        with mock.patch("daythread.cli.ApiServer") as api:
            api.return_value.serve_forever.side_effect = KeyboardInterrupt
            for argv in (
                ["--home", str(link), "serve"],
                ["--home", str(link), "ui", "--no-browser"],
            ):
                code, out, err = run_cli(argv)
                self.assertNotEqual(code, 0)
                self.assertIn("error:", err)
                self.assertIn("symlink", err)
                self.assertIn("rm " + shlex.quote(str(link)), err)
                self.assertNotIn("Traceback", err)
            api.assert_not_called()

        self.assertFalse((real / "daythread.db").exists())
        self.assertFalse((real / "api_token").exists())

    def test_non_server_commands_still_follow_symlinked_home(self):
        real = Path(self.home) / "real-home"
        real.mkdir()
        link = Path(self.home) / "linked home"
        link.symlink_to(real, target_is_directory=True)
        code, out, err = run_cli(["--home", str(link), "fact", "list"])
        self.assertEqual(code, 0, err)

    def test_serve_and_ui_accept_real_home(self):
        real = Path(self.home) / "real-home"
        real.mkdir()
        with mock.patch("daythread.cli.ApiServer") as api:
            api.return_value.serve_forever.side_effect = KeyboardInterrupt
            api.return_value.scheme = "http"
            api.return_value.port = 8765
            for argv in (
                ["--home", str(real), "serve"],
                ["--home", str(real), "ui", "--no-browser"],
            ):
                code, out, err = run_cli(argv)
                self.assertEqual(code, 0, err)
                self.assertNotIn("symlink", err)
        self.assertEqual(api.call_count, 2)

    def test_policy_deny_blocks_external(self):
        code, out, _ = run_cli(
            ["--home", self.home, "policy", "set", "external", "deny"]
        )
        self.assertEqual(code, 0)
        code, out, _ = run_cli(
            [
                "--home",
                self.home,
                "--json",
                "act",
                "message.send",
                "--arg",
                "to=maya@example.com",
            ]
        )
        self.assertEqual(code, 0)
        run = json.loads(out)
        self.assertEqual(run["state"], "denied")

    def test_unknown_tool_is_an_error(self):
        code, _, err = run_cli(["--home", self.home, "act", "nope.tool"])
        self.assertEqual(code, 1)
        self.assertIn("unknown tool", err)


if __name__ == "__main__":
    unittest.main()
