import sys
import tempfile
import unittest
from pathlib import Path

from daythread import mcp, runtime
from daythread.db import connect, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        mcp.add_server(self.conn, "echo", sys.executable, [str(FIXTURE)])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_server_tools_and_call(self):
        tools = mcp.server_tools(self.conn, "echo", cwd=self.home)
        self.assertEqual(tools[0]["name"], "echo")
        result = mcp.call(self.conn, "echo", "echo", {"text": "hello"}, cwd=self.home)
        self.assertEqual(result["content"][0]["text"], "hello")

    def test_mcp_tool_via_runtime(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "mcp.call",
            {"server": "echo", "tool": "echo", "arguments": {"text": "runtime"}},
        )
        self.assertEqual(run["state"], "pending_approval")
        run = runtime.approve(self.conn, self.home, run["id"])
        self.assertEqual(run["state"], "completed")
        executed = [step for step in run["steps"] if step["kind"] == "executed"][0]
        self.assertEqual(
            executed["payload"]["data"]["result"]["content"][0]["text"], "runtime"
        )

    def test_unknown_server(self):
        with self.assertRaises(KeyError):
            mcp.call(self.conn, "missing", "echo", {}, cwd=self.home)

    def test_remove(self):
        mcp.remove_server(self.conn, "echo")
        self.assertEqual(mcp.list_servers(self.conn), [])


if __name__ == "__main__":
    unittest.main()
