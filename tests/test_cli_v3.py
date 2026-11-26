import io
import json
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from daythread.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliV3Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_ask_and_model(self):
        code, out, _ = run_cli(["--home", self.home, "ask", "hello"])
        self.assertEqual(code, 0)
        self.assertIn("echo: hello", out)
        code, out, _ = run_cli(["--home", self.home, "model", "list"])
        self.assertEqual(code, 0)
        self.assertIn("local.echo", out)
        code, out, _ = run_cli(["--home", self.home, "model", "usage"])
        self.assertEqual(code, 0)
        self.assertIn("local.echo", out)

    def test_mcp_cli(self):
        command = "{} {}".format(shlex.quote(sys.executable), shlex.quote(str(FIXTURE)))
        code, out, _ = run_cli(
            ["--home", self.home, "mcp", "add", "echo", "--command", command]
        )
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["--home", self.home, "mcp", "tools", "echo"])
        self.assertEqual(code, 0)
        self.assertIn("echo", out)
        code, out, _ = run_cli(
            ["--home", self.home, "--json", "mcp", "call", "echo", "echo", "--arg", "text=hi"]
        )
        self.assertEqual(code, 0)
        run = json.loads(out)
        self.assertEqual(run["state"], "pending_approval")
        code, out, _ = run_cli(["--home", self.home, "approve", run["id"]])
        self.assertEqual(code, 0)
        self.assertIn("completed", out)

    def test_tick_and_notifications(self):
        code, out, _ = run_cli(["--home", self.home, "tick", "--job", "morning"])
        self.assertEqual(code, 0)
        self.assertIn("morning", out)
        code, out, _ = run_cli(["--home", self.home, "notifications"])
        self.assertEqual(code, 0)
        self.assertIn("morning", out)

    def test_backup_and_restore(self):
        run_cli(
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
        code, out, _ = run_cli(["--home", self.home, "backup"])
        self.assertEqual(code, 0)
        path = out.strip().split("backup written to ")[-1]
        self.assertTrue(Path(path).exists())
        code, out, _ = run_cli(["--home", self.home, "restore", path, "--verify"])
        self.assertEqual(code, 0)
        self.assertIn("backup OK", out)

        new_home = Path(self.home) / "new"
        code, out, _ = run_cli(["--home", str(new_home), "restore", path])
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["--home", str(new_home), "fact", "list"])
        self.assertIn("maya", out)

    def test_serve_print_and_rotate_token(self):
        code, first, _ = run_cli(["--home", self.home, "serve", "--print-token"])
        self.assertEqual(code, 0)
        code, second, _ = run_cli(
            ["--home", self.home, "serve", "--print-token", "--rotate-token"]
        )
        self.assertEqual(code, 0)
        self.assertNotEqual(first.strip(), second.strip())

    def test_daemon_single_tick(self):
        code, out, _ = run_cli(["--home", self.home, "daemon", "--max-ticks", "1"])
        self.assertEqual(code, 0)
        self.assertIn("daemon stopped", out)


if __name__ == "__main__":
    unittest.main()
