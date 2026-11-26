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


class CliV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_preview_and_execute(self):
        code, out, _ = run_cli(["--home", self.home, "--json", "plan", "send x@example.com | Hi | Body"])
        self.assertEqual(code, 0)
        preview = json.loads(out)
        self.assertTrue(preview["requires_approval"])
        self.assertEqual(len(preview["steps"]), 2)

        code, out, _ = run_cli(
            ["--home", self.home, "--json", "plan", "send x@example.com | Hi | Body", "--execute"]
        )
        self.assertEqual(code, 0)
        run = json.loads(out)
        self.assertEqual(run["state"], "pending_approval")
        code, out, _ = run_cli(["--home", self.home, "approve", run["id"]])
        self.assertEqual(code, 0)

    def test_connector_commands(self):
        code, out, _ = run_cli(["--home", self.home, "connector", "connect", "mock.gmail"])
        self.assertEqual(code, 0)
        self.assertIn("connected", out)
        code, out, _ = run_cli(["--home", self.home, "connector", "sync", "mock.gmail"])
        self.assertEqual(code, 0)
        self.assertIn("2 new", out)
        code, out, _ = run_cli(["--home", self.home, "connector", "list"])
        self.assertEqual(code, 0)
        self.assertIn("mock.gmail", out)

    def test_skill_commands(self):
        code, out, _ = run_cli(["--home", self.home, "skill", "list"])
        self.assertEqual(code, 0)
        self.assertIn("morning.brief", out)
        code, out, _ = run_cli(["--home", self.home, "--json", "skill", "run", "morning.brief"])
        self.assertEqual(code, 0)
        run = json.loads(out)
        self.assertEqual(run["state"], "completed")

    def test_memory_extract_and_conflicts(self):
        code, out, _ = run_cli(
            ["--home", self.home, "fact", "extract", "Maya is my co-founder.", "--apply"]
        )
        self.assertEqual(code, 0)
        self.assertIn("maya", out)
        run_cli(
            ["--home", self.home, "fact", "add", "--type", "person", "--subject", "maya", "--predicate", "timezone", "--value", "A"]
        )
        run_cli(
            ["--home", self.home, "fact", "add", "--type", "person", "--subject", "maya", "--predicate", "timezone", "--value", "B"]
        )
        code, out, _ = run_cli(["--home", self.home, "fact", "conflicts"])
        self.assertEqual(code, 0)
        self.assertIn("2 variants", out)

    def test_signals_and_proactive(self):
        code, out, _ = run_cli(
            [
                "--home",
                self.home,
                "signal",
                "add",
                "--source",
                "demo",
                "--kind",
                "note",
                "--payload",
                '{"title":"Review launch"}',
                "--dedupe-key",
                "k",
            ]
        )
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["--home", self.home, "--json", "proactive"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertTrue(any(item["kind"] == "signal:note" for item in result["candidates"]))
        target = result["candidates"][0]["id"]
        code, out, _ = run_cli(["--home", self.home, "feedback", target, "accept"])
        self.assertEqual(code, 0)

    def test_config_metrics_audit_doctor(self):
        code, out, _ = run_cli(["--home", self.home, "config", "set", "budget.max_steps", "5"])
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["--home", self.home, "config", "show"])
        self.assertEqual(code, 0)
        self.assertIn("budget.max_steps", out)
        code, out, _ = run_cli(["--home", self.home, "--json", "metrics"])
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(out), dict)
        code, out, _ = run_cli(["--home", self.home, "audit"])
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["--home", self.home, "doctor"])
        self.assertEqual(code, 0)
        self.assertIn("integrity", out)


if __name__ == "__main__":
    unittest.main()
