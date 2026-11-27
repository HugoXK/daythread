import platform
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from daythread import runtime, sandbox
from daythread.db import connect, init_db, set_setting

HAS_SANDBOX = platform.system() == "Darwin" and shutil.which("sandbox-exec")


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_default_is_none(self):
        command = ["echo", "hi"]
        self.assertEqual(sandbox.wrap(command, self.conn, self.home), command)

    def test_unknown_backend(self):
        set_setting(self.conn, "sandbox.backend", "bogus")
        with self.assertRaises(Exception):
            sandbox.wrap(["echo"], self.conn, self.home)

    @unittest.skipUnless(HAS_SANDBOX, "sandbox-exec is not available")
    def test_profile_wraps_command(self):
        set_setting(self.conn, "sandbox.backend", "sandbox-exec")
        wrapped = sandbox.wrap(["echo", "hi"], self.conn, self.home, allow_network=False)
        self.assertEqual(wrapped[0], shutil.which("sandbox-exec"))
        self.assertEqual(wrapped[1], "-p")
        self.assertIn("deny network*", wrapped[2])
        self.assertIn(str(self.home.resolve()), wrapped[2])

    def test_bwrap_wrap(self):
        with mock.patch("daythread.sandbox.shutil.which", return_value="/usr/bin/bwrap"):
            set_setting(self.conn, "sandbox.backend", "bwrap")
            wrapped = sandbox.wrap(["echo", "hi"], self.conn, self.home, allow_network=False)
        self.assertEqual(wrapped[0], "/usr/bin/bwrap")
        self.assertIn("--unshare-net", wrapped)
        self.assertTrue(any(str(self.home.resolve()) in part for part in wrapped))

    def test_docker_wrap(self):
        if not shutil.which("docker"):
            self.skipTest("docker is not installed")
        set_setting(self.conn, "sandbox.backend", "docker")
        wrapped = sandbox.wrap(["echo", "hi"], self.conn, self.home, allow_network=False)
        self.assertEqual(wrapped[0], shutil.which("docker"))
        self.assertIn("--network", wrapped)
        self.assertEqual(wrapped[wrapped.index("--network") + 1], "none")
        self.assertTrue(any(str(self.home.resolve()) in part for part in wrapped))

    @unittest.skipUnless(HAS_SANDBOX, "sandbox-exec is not available")
    def test_shell_runs_in_sandbox(self):
        set_setting(self.conn, "sandbox.backend", "sandbox-exec")
        run = runtime.propose(self.conn, self.home, "shell.run", {"command": "echo sandboxed"})
        self.assertEqual(run["state"], "pending_approval")
        run = runtime.approve(self.conn, self.home, run["id"])
        self.assertEqual(run["state"], "completed")
        executed = [step for step in run["steps"] if step["kind"] == "executed"][0]
        self.assertIn("sandboxed", executed["payload"]["data"]["stdout"])


if __name__ == "__main__":
    unittest.main()
