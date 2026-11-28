"""Undo contract: external writes must be reversible or explicitly irreversible."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from daythread import connectors, receipt, runtime, tools
from daythread.db import connect, init_db
from daythread.errors import ProviderError
from daythread.ledger import get_steps, verify_run


class FakeGoogleCalendar:
    """Deterministic fake for google.calendar create/move/delete; no socket."""

    def __init__(self):
        self.events = []
        self.calls = []
        self.fail_delete = False

    def __call__(self, request):
        method = request.get_method()
        url = request.full_url
        self.calls.append((method, url))
        if method == "POST" and url.endswith("/events"):
            event = {"id": "evt-1", "summary": "Review"}
            self.events.append(event)
            return json.dumps(event).encode("utf-8")
        if method == "PATCH":
            return json.dumps({"id": url.rsplit("/", 1)[-1]}).encode("utf-8")
        if method == "DELETE":
            if self.fail_delete:
                raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
            event_id = url.rsplit("/", 1)[-1]
            self.events = [item for item in self.events if item.get("id") != event_id]
            return b""
        raise AssertionError("unexpected fake request: {} {}".format(method, url))

    def deletes(self):
        return [url.rsplit("/", 1)[-1] for method, url in self.calls if method == "DELETE"]


class UndoContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "daythread.db")
        init_db(self.conn)
        self.conn.execute(
            "INSERT INTO connectors(id, version, status, scopes, connected_at)"
            " VALUES('google.calendar', '1.1.0', 'connected', '[]', '2026-01-01')"
        )
        self.conn.commit()
        self.opener = FakeGoogleCalendar()
        self._patches = [
            mock.patch.object(
                connectors, "_get_token", return_value={"access_token": "fake"}
            ),
            mock.patch.object(connectors, "HTTP_OPENER", self.opener),
            mock.patch.object(runtime, "_run_isolated", return_value=None),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self._patches):
            patcher.stop()
        self.conn.close()
        self.tmp.cleanup()

    def _local_calendar(self):
        return [
            tuple(row)
            for row in self.conn.execute(
                "SELECT id, title, start, end FROM calendar_events ORDER BY id"
            ).fetchall()
        ]

    def _create_event(self, title="Review"):
        run = runtime.propose(
            self.conn,
            self.home,
            "connector.call",
            {
                "connector": "google.calendar",
                "action": "create_event",
                "arguments": {"title": title, "start": "2026-11-28T10:00:00Z"},
            },
        )
        self.assertEqual(run["state"], "pending_approval")
        return runtime.approve(self.conn, self.home, run["id"])

    # --- registry contract -------------------------------------------------

    def test_registry_rejects_external_tool_without_undo_contract(self):
        bad = tools.ToolSpec(
            "test.external", "external", "bad", lambda ctx, args: tools.ToolResult("x")
        )
        with self.assertRaises(ValueError) as caught:
            tools._check_undo_contract({"test.external": bad})
        message = str(caught.exception)
        self.assertIn("test.external", message)
        self.assertIn("external", message)

    def test_registry_rejects_irreversible_without_reason(self):
        bad = tools.ToolSpec(
            "test.irreversible",
            "destructive",
            "bad",
            lambda ctx, args: tools.ToolResult("x"),
            irreversible=True,
        )
        with self.assertRaises(ValueError):
            tools._check_undo_contract({"test.irreversible": bad})

    def test_registry_rejects_both_undo_and_irreversible(self):
        bad = tools.ToolSpec(
            "test.both",
            "external",
            "bad",
            lambda ctx, args: tools.ToolResult("x"),
            provides_undo=True,
            irreversible=True,
            irreversible_reason="nope",
        )
        with self.assertRaises(ValueError):
            tools._check_undo_contract({"test.both": bad})

    def test_irreversible_whitelist_is_explicit_and_complete(self):
        for spec in tools.TOOLS.values():
            if spec.risk in tools.UNDO_REQUIRED_RISKS:
                self.assertTrue(
                    spec.provides_undo or spec.irreversible_reason.strip(),
                    "{} has no undo contract".format(spec.name),
                )
            if spec.irreversible_reason:
                self.assertIn(spec.name, tools.IRREVERSIBLE_TOOLS)
        for name, reason in tools.IRREVERSIBLE_TOOLS.items():
            self.assertIn(name, tools.TOOLS)
            self.assertTrue(reason.strip(), name)

    # --- connector recipe and write -> undo -> baseline --------------------

    def test_create_event_carries_executable_undo_recipe(self):
        run = self._create_event()
        executed = [
            step for step in get_steps(self.conn, run["id"]) if step["kind"] == "executed"
        ][0]
        recipe = executed["payload"]["undo"]
        self.assertEqual(recipe["kind"], "connector.delete")
        self.assertEqual(recipe["connector"], "google.calendar")
        self.assertEqual(recipe["action"], "delete_event")
        self.assertEqual(recipe["target"], {"event_id": "evt-1"})
        self.assertFalse(executed["payload"]["irreversible"])
        self.assertTrue(recipe["idempotency_key"])

    def test_undo_restores_remote_and_local_baseline_without_socket(self):
        local_before = self._local_calendar()
        remote_before = list(self.opener.events)
        run = self._create_event()
        self.assertEqual([item["id"] for item in self.opener.events], ["evt-1"])

        with mock.patch(
            "socket.socket", side_effect=AssertionError("real socket opened")
        ):
            undone = runtime.undo_run(self.conn, self.home, run["id"])

        self.assertEqual(undone["state"], "undone")
        self.assertEqual(self.opener.events, remote_before)
        self.assertEqual(self._local_calendar(), local_before)
        self.assertEqual(self.opener.deletes(), ["evt-1"])

    def test_second_undo_is_a_noop(self):
        run = self._create_event()
        runtime.undo_run(self.conn, self.home, run["id"])
        executed = [
            step for step in get_steps(self.conn, run["id"]) if step["kind"] == "executed"
        ][0]
        deletes_before = len(self.opener.deletes())
        ctx = tools.ToolContext(conn=self.conn, home=self.home, run_id=run["id"])
        summary = tools.apply_undo(ctx, executed["payload"]["undo"])
        self.assertIn("already undone", summary)
        self.assertEqual(len(self.opener.deletes()), deletes_before)

    def test_undo_failure_marks_run_undo_failed_and_keeps_payload(self):
        run = self._create_event()
        before = get_steps(self.conn, run["id"])
        self.opener.fail_delete = True
        with self.assertRaises(ProviderError):
            runtime.undo_run(self.conn, self.home, run["id"])
        current = runtime.get_run(self.conn, run["id"])
        self.assertEqual(current["state"], "undo_failed")
        after = get_steps(self.conn, run["id"])
        self.assertEqual(after[-1]["kind"], "undo.failed")
        self.assertIn("403", after[-1]["payload"]["error"])
        executed_before = [step for step in before if step["kind"] == "executed"][0]
        executed_after = [step for step in after if step["kind"] == "executed"][0]
        self.assertEqual(executed_before, executed_after)
        ok, errors = verify_run(self.conn, run["id"])
        self.assertTrue(ok, errors)

    def test_action_without_undo_recipe_is_explicitly_irreversible(self):
        result = connectors.call_action(
            self.conn,
            self.home,
            "google.calendar",
            "move_event",
            {"event_id": "evt-1", "start": "2026-11-28T11:00:00Z"},
            opener=self.opener,
        )
        self.assertIsNone(result["undo"])
        self.assertTrue(result["irreversible"])
        self.assertIn("no declared undo recipe", result["irreversible_reason"])

    # --- receipt projection ------------------------------------------------

    def test_receipt_shows_executable_undo_command(self):
        run = self._create_event()
        data = receipt.build_receipt(self.conn, run["id"])
        self.assertTrue(data["undo"]["available"])
        self.assertFalse(data["undo"]["irreversible"])
        recipe = data["undo"]["recipes"][0]
        self.assertEqual(recipe["connector"], "google.calendar")
        self.assertEqual(recipe["action"], "delete_event")
        self.assertEqual(recipe["target"], {"event_id": "evt-1"})
        markdown = receipt.to_markdown(data)
        self.assertIn("dt undo {}".format(run["id"]), markdown)

    def test_receipt_marks_unsupported_write_irreversible(self):
        run = runtime.propose(
            self.conn,
            self.home,
            "connector.call",
            {
                "connector": "google.calendar",
                "action": "move_event",
                "arguments": {"event_id": "evt-1", "start": "2026-11-28T11:00:00Z"},
            },
        )
        run = runtime.approve(self.conn, self.home, run["id"])
        data = receipt.build_receipt(self.conn, run["id"])
        self.assertFalse(data["undo"]["available"])
        self.assertTrue(data["undo"]["irreversible"])

    # --- no-op duplicate paths --------------------------------------------

    def test_duplicate_task_is_a_noop_and_undo_succeeds(self):
        first = runtime.propose(
            self.conn, self.home, "task.create", {"id": "task-1", "title": "Same"}
        )
        self.assertEqual(first["state"], "completed")
        second = runtime.propose(
            self.conn, self.home, "task.create", {"id": "task-1", "title": "Same"}
        )
        self.assertEqual(second["state"], "completed")
        executed = [
            step
            for step in get_steps(self.conn, second["id"])
            if step["kind"] == "executed"
        ][0]
        self.assertTrue(executed["payload"]["no_op"])
        undone = runtime.undo_run(self.conn, self.home, second["id"])
        self.assertEqual(undone["state"], "undone")
        rows = self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        self.assertEqual(rows[0], 1)

    def test_duplicate_memory_fact_is_a_noop_and_undo_succeeds(self):
        args = {
            "subject_type": "self",
            "subject_id": "self",
            "predicate": "city",
            "value": "Shanghai",
        }
        first = runtime.propose(self.conn, self.home, "memory.remember", dict(args))
        self.assertEqual(first["state"], "completed")
        second = runtime.propose(self.conn, self.home, "memory.remember", dict(args))
        executed = [
            step
            for step in get_steps(self.conn, second["id"])
            if step["kind"] == "executed"
        ][0]
        self.assertTrue(executed["payload"]["no_op"])
        undone = runtime.undo_run(self.conn, self.home, second["id"])
        self.assertEqual(undone["state"], "undone")
        rows = self.conn.execute(
            "SELECT COUNT(*) FROM facts WHERE predicate = 'city'"
        ).fetchone()
        self.assertEqual(rows[0], 1)


if __name__ == "__main__":
    unittest.main()
