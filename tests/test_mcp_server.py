import sys
import tempfile
import unittest
from pathlib import Path

from daythread import mcp, memory, runtime
from daythread.db import connect, init_db
from daythread.errors import McpError

ROOT = Path(__file__).resolve().parent.parent


class McpServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.db_path = self.home / "daythread.db"
        conn = connect(self.db_path)
        init_db(conn)
        memory.add_fact(conn, "person", "maya", "role", "co-founder")
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _client(self, allow_write=False):
        command = [
            sys.executable,
            "-m",
            "daythread",
            "--home",
            str(self.home),
            "mcp",
            "serve",
        ]
        if allow_write:
            command.append("--allow-write")
        return mcp.McpStdioClient(command, cwd=ROOT, timeout=10)

    def test_read_only_tools_and_search(self):
        client = self._client()
        try:
            client.start()
            client.initialize()
            names = {tool["name"] for tool in client.list_tools()}
            self.assertIn("daythread.memory_search", names)
            self.assertNotIn("daythread.memory_remember", names)
            result = client.call_tool("daythread.memory_search", {"q": "co-founder"})
            self.assertIn("maya", result["content"][0]["text"])
        finally:
            client.close()

    def test_read_only_blocks_writes(self):
        client = self._client()
        try:
            client.start()
            client.initialize()
            with self.assertRaises(McpError):
                client.call_tool(
                    "daythread.memory_remember", {"text": "Maya is my co-founder"}
                )
        finally:
            client.close()

    def test_allow_write_remembers_and_proposes(self):
        client = self._client(allow_write=True)
        try:
            client.start()
            client.initialize()
            names = {tool["name"] for tool in client.list_tools()}
            self.assertIn("daythread.memory_remember", names)
            client.call_tool(
                "daythread.memory_remember", {"text": "Maya is my co-founder"}
            )
            result = client.call_tool(
                "daythread.propose_plan",
                {
                    "steps": [
                        {
                            "id": "s1",
                            "tool": "note.write",
                            "args": {"path": "mcp.md", "content": "hello"},
                        }
                    ],
                    "intent": "mcp proposal",
                },
            )
            import json

            proposal = json.loads(result["content"][0]["text"])
            self.assertEqual(proposal["state"], "pending_approval")
            conn = connect(self.db_path)
            try:
                approved = runtime.approve(conn, self.home, proposal["id"])
                self.assertEqual(approved["state"], "completed")
            finally:
                conn.close()
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
