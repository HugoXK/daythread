import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

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
