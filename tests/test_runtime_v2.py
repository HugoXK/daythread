import tempfile
import unittest
from pathlib import Path

from daythread import runtime
from daythread.db import connect, init_db, set_setting
from daythread.ledger import verify_run
from daythread.tools import TOOLS, ToolResult, ToolSpec


class RuntimeV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        self._tools = dict(TOOLS)

    def tearDown(self):
        TOOLS.clear()
        TOOLS.update(self._tools)
        self.conn.close()
        self.tmp.cleanup()

    def test_multi_step_and_undo(self):
        steps = [
            {"id": "s1", "tool": "note.write", "args": {"path": "a.md", "content": "A"}},
            {"id": "s2", "tool": "note.write", "args": {"path": "b.md", "content": "B"}, "depends_on": ["s1"]},
        ]
        run = runtime.propose_plan(self.conn, self.home, steps, intent="notes")
        self.assertEqual(run["state"], "completed")
        self.assertTrue((self.home / "notes" / "a.md").exists())
        self.assertTrue((self.home / "notes" / "b.md").exists())
        ok, errors = verify_run(self.conn, run["id"])
        self.assertTrue(ok, errors)
        undone = runtime.undo_run(self.conn, self.home, run["id"])
        self.assertEqual(undone["state"], "undone")
        self.assertFalse((self.home / "notes" / "a.md").exists())
        self.assertFalse((self.home / "notes" / "b.md").exists())

    def test_idempotency_key(self):
        steps = [{"id": "s1", "tool": "note.write", "args": {"path": "a.md", "content": "A"}}]
        first = runtime.propose_plan(self.conn, self.home, steps, idempotency_key="same")
        second = runtime.propose_plan(self.conn, self.home, steps, idempotency_key="same")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(runtime.list_runs(self.conn)), 1)

    def test_plan_approval_gates_all_steps(self):
        steps = [
            {"id": "s1", "tool": "message.draft", "args": {"to": "maya@example.com", "subject": "Deck"}},
            {"id": "s2", "tool": "message.send", "args": {"to": "maya@example.com", "subject": "Deck"}, "depends_on": ["s1"]},
        ]
        run = runtime.propose_plan(self.conn, self.home, steps, intent="send")
        self.assertEqual(run["state"], "pending_approval")
        self.assertFalse((self.home / "outbox" / "sent.jsonl").exists())
        approved = runtime.approve(self.conn, self.home, run["id"])
        self.assertEqual(approved["state"], "completed")
        self.assertTrue((self.home / "outbox" / "sent.jsonl").exists())

    def test_retry_then_success(self):
        calls = {"n": 0}

        def flaky(ctx, args):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return ToolResult("ok")

        TOOLS["test.flaky"] = ToolSpec("test.flaky", "write", "flaky", flaky, "test")
        run = runtime.propose_plan(
            self.conn,
            self.home,
            [{"id": "s1", "tool": "test.flaky", "args": {}, "max_attempts": 2}],
        )
        self.assertEqual(run["state"], "completed")
        self.assertEqual(calls["n"], 2)
        kinds = [step["kind"] for step in run["steps"]]
        self.assertIn("execution_failed", kinds)
        self.assertIn("executed", kinds)

    def test_budget_stops_plan(self):
        set_setting(self.conn, "budget.max_steps", "1")
        steps = [
            {"id": "s1", "tool": "note.write", "args": {"path": "a.md", "content": "A"}},
            {"id": "s2", "tool": "note.write", "args": {"path": "b.md", "content": "B"}},
        ]
        run = runtime.propose_plan(self.conn, self.home, steps)
        self.assertEqual(run["state"], "failed")
        self.assertIn("max_steps", run["error"])
        self.assertFalse((self.home / "notes" / "b.md").exists())

    def test_resume_after_failure(self):
        calls = {"n": 0}

        def flaky(ctx, args):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first attempt fails")
            return ToolResult("ok")

        TOOLS["test.once"] = ToolSpec("test.once", "write", "fails once", flaky, "test")
        run = runtime.propose_plan(
            self.conn,
            self.home,
            [{"id": "s1", "tool": "test.once", "args": {}, "max_attempts": 1}],
        )
        self.assertEqual(run["state"], "failed")
        resumed = runtime.resume_run(self.conn, self.home, run["id"])
        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(calls["n"], 2)

    def test_shell_is_allowlisted(self):
        run = runtime.propose(self.conn, self.home, "shell.run", {"command": "echo hello"})
        self.assertEqual(run["state"], "pending_approval")
        run = runtime.approve(self.conn, self.home, run["id"])
        self.assertEqual(run["state"], "completed")
        executed = [step for step in run["steps"] if step["kind"] == "executed"][0]
        self.assertIn("hello", executed["payload"]["data"]["stdout"])

        blocked = runtime.propose(self.conn, self.home, "shell.run", {"command": "cat /etc/passwd"})
        blocked = runtime.approve(self.conn, self.home, blocked["id"])
        self.assertEqual(blocked["state"], "failed")
        self.assertIn("absolute paths", blocked["error"])

    def test_force_approval_even_for_write(self):
        run = runtime.propose_plan(
            self.conn,
            self.home,
            [{"id": "s1", "tool": "note.write", "args": {"path": "a.md", "content": "A"}}],
            force_approval=True,
        )
        self.assertEqual(run["state"], "pending_approval")
        run = runtime.approve(self.conn, self.home, run["id"])
        self.assertEqual(run["state"], "completed")

    def test_lease_is_atomic(self):
        run = runtime.propose(self.conn, self.home, "message.send", {"to": "x@example.com"})
        self.conn.execute("UPDATE runs SET state = 'approved' WHERE id = ?", (run["id"],))
        self.conn.commit()
        self.assertTrue(runtime._claim_run(self.conn, run["id"], "owner-a"))
        other = connect(self.home / "db.sqlite")
        try:
            self.assertFalse(runtime._claim_run(other, run["id"], "owner-b"))
        finally:
            other.close()

    def test_lease_released_after_completion(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "a.md", "content": "A"},
        )
        self.assertEqual(run["state"], "completed")
        row = self.conn.execute(
            "SELECT lease_owner, lease_until FROM runs WHERE id = ?", (run["id"],)
        ).fetchone()
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_until"])

    def test_step_timeout(self):
        import time as _time

        def slow(ctx, args):
            _time.sleep(2)
            return ToolResult("late")

        TOOLS["test.slow"] = ToolSpec("test.slow", "write", "slow", slow, "test")
        set_setting(self.conn, "runtime.step_timeout_seconds", "0.1")
        run = runtime.propose(
            self.conn,
            self.home,
            "test.slow",
            {},
        )
        self.assertEqual(run["state"], "failed")
        self.assertIn("timed out", run["error"])

    def test_cancel_pending(self):
        run = runtime.propose(self.conn, self.home, "message.send", {"to": "maya@example.com"})
        self.assertEqual(run["state"], "pending_approval")
        cancelled = runtime.cancel_run(self.conn, run["id"])
        self.assertEqual(cancelled["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
