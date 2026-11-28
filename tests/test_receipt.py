import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from daythread import memory, receipt, runtime
from daythread.cli import main
from daythread.db import canonical, connect, init_db, utc_now
from daythread.ledger import append_step, get_steps, verify_run


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:  # argparse errors
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class ReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "daythread.db")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _run(self, tool="note.write", args=None, approve=False):
        run = runtime.propose(
            self.conn,
            self.home,
            tool,
            args if args is not None else {"path": "launch.md", "content": "hello"},
            intent="receipt test",
        )
        if approve:
            run = runtime.approve(self.conn, self.home, run["id"])
        return run

    def _raw_run(self, run_id, state="completed", steps=None):
        now = utc_now()
        self.conn.execute(
            "INSERT INTO runs(id, intent, tool, args, state, risk, decision, created_at, updated_at)"
            " VALUES(?, 'x', 'note.write', '{}', ?, 'write', 'allow', ?, ?)",
            (run_id, state, now, now),
        )
        self.conn.commit()
        for kind, payload in steps or []:
            append_step(self.conn, run_id, kind, payload)
        return now

    def _digest_body(self, data):
        body = dict(data)
        body.pop("digest")
        return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()

    def test_receipt_shape_and_digest(self):
        run = self._run()
        data = receipt.build_receipt(self.conn, run["id"])

        self.assertEqual(
            set(data),
            {"version", "run", "approvals", "steps", "chain_head", "policy", "undo", "digest"},
        )
        self.assertEqual(data["version"], 1)
        self.assertEqual(
            set(data["run"]), {"id", "plan_id", "state", "created_at", "finished_at"}
        )
        self.assertEqual(data["run"]["id"], run["id"])
        self.assertEqual(data["run"]["state"], run["state"])
        self.assertIsNone(data["run"]["plan_id"])
        self.assertEqual(data["approvals"], [])

        steps = get_steps(self.conn, run["id"])
        self.assertEqual(len(data["steps"]), len(steps))
        self.assertEqual(data["chain_head"], steps[-1]["hash"])
        self.assertEqual(
            [item["seq"] for item in data["steps"]], [item["seq"] for item in steps]
        )
        for item in data["steps"]:
            self.assertEqual(
                set(item), {"seq", "kind", "hash", "prev_hash", "payload_digest"}
            )
        self.assertEqual(data["policy"], {"risk": run["risk"], "decision": run["decision"]})
        self.assertEqual(data["digest"], self._digest_body(data))

    def test_receipt_is_deterministic(self):
        run = self._run()
        first = receipt.to_json(receipt.build_receipt(self.conn, run["id"]))
        second = receipt.to_json(receipt.build_receipt(self.conn, run["id"]))
        self.assertEqual(first, second)
        path = self.home / "daythread.db"
        self.conn.close()
        self.conn = connect(path)
        third = receipt.to_json(receipt.build_receipt(self.conn, run["id"]))
        self.assertEqual(first, third)

    def test_receipt_excludes_payload_plaintext(self):
        canary = "sk-receipt-canary-2f7c1a"
        run = self._run(args={"path": "secret.md", "content": canary})
        text = receipt.to_json(receipt.build_receipt(self.conn, run["id"]))
        self.assertNotIn(canary, text)
        self.assertIn("payload_digest", text)

    def test_receipt_redacts_sensitive_recipe_keys(self):
        token = "ghp-receipt-token-canary"
        api_key = "api-key-canary-9931"
        self._raw_run(
            "sensitive-run",
            steps=[
                (
                    "executed",
                    {
                        "undo": {
                            "kind": "connector.delete",
                            "api_key": api_key,
                            "target": {"event_id": "evt-1", "token": token},
                        }
                    },
                )
            ],
        )
        data = receipt.build_receipt(self.conn, "sensitive-run")
        text = receipt.to_json(data)
        self.assertNotIn(token, text)
        self.assertNotIn(api_key, text)
        self.assertIn("[REDACTED]", text)
        self.assertEqual(
            data["undo"]["recipes"][0]["target"],
            {"event_id": "evt-1", "token": "[REDACTED]"},
        )

    def test_receipt_excludes_secret_and_health_facts(self):
        secret = memory.add_fact(
            self.conn, "self", "self", "api_token", "sk-secret-value", sensitivity="secret"
        )
        health = memory.add_fact(
            self.conn, "self", "self", "diagnosis", "migraine", sensitivity="health"
        )
        normal = memory.add_fact(self.conn, "self", "self", "city", "Shanghai")
        self._raw_run(
            "facts-run",
            steps=[
                (
                    "executed",
                    {
                        "undo": {
                            "kind": "facts.delete",
                            "ids": [secret["id"], health["id"], normal["id"]],
                        }
                    },
                )
            ],
        )
        data = receipt.build_receipt(self.conn, "facts-run")
        ids = data["undo"]["recipes"][0]["ids"]
        self.assertEqual(ids, [normal["id"]])
        text = receipt.to_json(data)
        self.assertNotIn(secret["id"], text)
        self.assertNotIn(health["id"], text)

    def test_receipt_drops_recipe_without_remaining_fact_ids(self):
        secret = memory.add_fact(
            self.conn, "self", "self", "password", "hunter2", sensitivity="secret"
        )
        self._raw_run(
            "only-secret-run",
            steps=[("executed", {"undo": {"kind": "facts.delete", "ids": [secret["id"]]}})],
        )
        data = receipt.build_receipt(self.conn, "only-secret-run")
        self.assertEqual(data["undo"]["recipes"], [])
        self.assertFalse(data["undo"]["available"])

    def test_undo_recipe_never_inlines_previous_content(self):
        old_content = "CANARY-OLD-SECRET-VALUE"
        run = self._run(args={"path": "notes/plan.md", "content": old_content})
        self.assertEqual(run["state"], "completed")
        second = self._run(args={"path": "notes/plan.md", "content": "new content"})
        data = receipt.build_receipt(self.conn, second["id"])
        text = receipt.to_json(data)
        self.assertNotIn(old_content, text)
        recipe = data["undo"]["recipes"][0]
        self.assertEqual(recipe["kind"], "file.write")
        self.assertEqual(recipe["path"], "notes/plan.md")
        self.assertNotIn("previous", recipe)
        self.assertNotIn("fields", recipe)

    def test_approval_record_is_projected(self):
        run = self._run("message.send", {"to": "maya@example.com", "body": "hi"})
        run = runtime.approve(self.conn, self.home, run["id"], approver="alice-reviewer")
        data = receipt.build_receipt(self.conn, run["id"])
        self.assertEqual(len(data["approvals"]), 1)
        approval = data["approvals"][0]
        self.assertEqual(approval["decision"], "approved")
        self.assertEqual(approval["approver"], "alice-reviewer")
        self.assertTrue(approval["ts"])
        self.assertIn("alice-reviewer", receipt.to_json(data))
        self.assertIn("alice-reviewer", receipt.to_markdown(data))

    def test_sensitive_fact_ids_survive_many_ids(self):
        many = ["fact-{}".format(index) for index in range(1500)]
        self.assertEqual(receipt._sensitive_fact_ids(self.conn, many), set())

    def test_tampered_ledger_changes_receipt_digest(self):
        run = self._run()
        before = receipt.build_receipt(self.conn, run["id"])
        self.conn.execute(
            "UPDATE run_steps SET payload = ? WHERE run_id = ? AND kind = 'executed'",
            ('{"tampered":true}', run["id"]),
        )
        self.conn.commit()
        ok, errors = verify_run(self.conn, run["id"])
        self.assertFalse(ok)
        self.assertTrue(errors)
        after = receipt.build_receipt(self.conn, run["id"])
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertEqual(before["chain_head"], after["chain_head"])

    def test_undo_projection_for_reversible_run(self):
        run = self._run()
        data = receipt.build_receipt(self.conn, run["id"])
        self.assertTrue(data["undo"]["available"])
        self.assertFalse(data["undo"]["irreversible"])
        self.assertTrue(data["undo"]["recipes"])
        self.assertEqual(data["undo"]["recipes"][0]["kind"], "file.write")

    def test_undo_projection_for_irreversible_run(self):
        run = self._run("message.send", {"to": "maya@example.com", "body": "hi"})
        self.assertEqual(run["state"], "pending_approval")
        run = runtime.approve(self.conn, self.home, run["id"])
        self.assertEqual(run["state"], "completed")
        data = receipt.build_receipt(self.conn, run["id"])
        self.assertFalse(data["undo"]["available"])
        self.assertTrue(data["undo"]["irreversible"])
        self.assertEqual(data["undo"]["recipes"], [])

    def test_receipt_does_not_write_ledger_or_run_state(self):
        run = self._run()
        before_run = self.conn.execute(
            "SELECT state, updated_at FROM runs WHERE id = ?", (run["id"],)
        ).fetchone()
        before_steps = get_steps(self.conn, run["id"])
        receipt.build_receipt(self.conn, run["id"])
        receipt.build_receipt(self.conn, run["id"])
        after_run = self.conn.execute(
            "SELECT state, updated_at FROM runs WHERE id = ?", (run["id"],)
        ).fetchone()
        self.assertEqual(tuple(before_run), tuple(after_run))
        self.assertEqual(before_steps, get_steps(self.conn, run["id"]))

    def test_empty_run(self):
        now = self._raw_run("empty-run")
        data = receipt.build_receipt(self.conn, "empty-run")
        self.assertEqual(data["steps"], [])
        self.assertEqual(data["chain_head"], "")
        self.assertFalse(data["undo"]["available"])
        self.assertEqual(data["run"]["finished_at"], now)
        self.assertEqual(data["digest"], self._digest_body(data))

    def test_single_step_run(self):
        self._raw_run(
            "solo-run", steps=[("executed", {"undo": {"kind": "file.write", "path": "a.md"}})]
        )
        data = receipt.build_receipt(self.conn, "solo-run")
        self.assertEqual(len(data["steps"]), 1)
        self.assertEqual(data["steps"][0]["hash"], data["chain_head"])
        self.assertEqual(data["steps"][0]["prev_hash"], "")
        expected = hashlib.sha256(
            canonical({"undo": {"kind": "file.write", "path": "a.md"}}).encode("utf-8")
        ).hexdigest()
        self.assertEqual(data["steps"][0]["payload_digest"], expected)

    def test_unknown_run_raises_key_error(self):
        with self.assertRaises(KeyError):
            receipt.build_receipt(self.conn, "nope")

    def test_unfinished_run_raises(self):
        run = self._run("message.send", {"to": "maya@example.com", "body": "hi"})
        self.assertEqual(run["state"], "pending_approval")
        with self.assertRaises(ValueError) as ctx:
            receipt.build_receipt(self.conn, run["id"])
        self.assertIn("finished", str(ctx.exception))

    def test_markdown_output(self):
        run = self._run()
        data = receipt.build_receipt(self.conn, run["id"])
        text = receipt.to_markdown(data)
        self.assertIn("# Receipt", text)
        self.assertIn(run["id"], text)
        self.assertIn(data["digest"], text)
        self.assertIn("chain_head", text)
        self.assertIn("prev_hash", text)
        self.assertIn("## Approvals", text)
        for step in data["steps"]:
            self.assertIn(step["hash"], text)
            self.assertIn(step["prev_hash"] or "-", text)
        self.assertEqual(len(data["steps"][0]["hash"]), 64)
        self.assertEqual(len(data["steps"][-1]["hash"]), 64)


class ReceiptCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "daythread.db")
        init_db(self.conn)
        self.run = runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "launch.md", "content": "hello"},
            intent="receipt cli",
        )
        self.pending = runtime.propose(
            self.conn,
            self.home,
            "message.send",
            {"to": "maya@example.com", "body": "hi"},
            intent="pending",
        )
        self.conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_defaults_to_markdown(self):
        code, out, err = run_cli(["--home", str(self.home), "receipt", self.run["id"]])
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("# Receipt"))
        self.assertIn(self.run["id"], out)

    def test_cli_json_flag(self):
        code, out, err = run_cli(
            ["--home", str(self.home), "--json", "receipt", self.run["id"]]
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["run"]["id"], self.run["id"])
        self.assertEqual(out.strip(), receipt.to_json(data))

    def test_cli_out_file_matches_stdout(self):
        out_path = self.home / "receipt.md"
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", self.run["id"], "--out", str(out_path)]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out_path.read_text(encoding="utf-8"), out)
        self.assertTrue(out.startswith("# Receipt"))

    def test_cli_json_out_file_matches_stdout(self):
        out_path = self.home / "receipt.json"
        code, out, err = run_cli(
            [
                "--home",
                str(self.home),
                "--json",
                "receipt",
                self.run["id"],
                "--out",
                str(out_path),
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out_path.read_text(encoding="utf-8"), out)
        json.loads(out)

    def test_cli_keeps_run_state(self):
        code, _, err = run_cli(["--home", str(self.home), "receipt", self.run["id"]])
        self.assertEqual(code, 0, err)
        conn = connect(self.home / "daythread.db")
        try:
            row = conn.execute(
                "SELECT state, updated_at FROM runs WHERE id = ?", (self.run["id"],)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["state"], "completed")

    def test_cli_unknown_run_exits_nonzero_without_traceback(self):
        code, out, err = run_cli(["--home", str(self.home), "receipt", "missing"])
        self.assertEqual(code, 1)
        self.assertIn("error: run not found: missing", err)
        self.assertNotIn("'run not found", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")

    def test_cli_unfinished_run_exits_nonzero_without_receipt(self):
        code, out, err = run_cli(["--home", str(self.home), "receipt", self.pending["id"]])
        self.assertEqual(code, 1)
        self.assertIn("finished", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()


class ReceiptVerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "daythread.db")
        init_db(self.conn)
        self.run = runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "launch.md", "content": "hello"},
            intent="verify test",
        )
        self.steps = get_steps(self.conn, self.run["id"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _receipt(self):
        return receipt.build_receipt(self.conn, self.run["id"])

    def _tampered(self, mutate):
        data = json.loads(receipt.to_json(self._receipt()))
        mutate(data)
        return data

    def test_clean_receipt_verifies(self):
        result = receipt.verify_receipt(self._receipt())
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["first_broken_seq"])

    def test_missing_digest_is_rejected(self):
        data = self._receipt()
        data.pop("digest")
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertIn("digest", " ".join(result["errors"]).lower())

    def test_version_mismatch_is_rejected(self):
        data = self._receipt()
        data["version"] = 99
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertIn("version", " ".join(result["errors"]).lower())

    def test_missing_step_field_is_rejected(self):
        data = self._tampered(lambda item: item["steps"][1].pop("prev_hash"))
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertIn("prev_hash", " ".join(result["errors"]))

    def test_tampered_payload_digest_breaks_digest(self):
        data = self._tampered(lambda item: item["steps"][2].__setitem__("payload_digest", "0" * 64))
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertIn("digest", " ".join(result["errors"]).lower())

    def test_tampered_prev_hash_points_at_first_broken_step(self):
        data = self._tampered(lambda item: item["steps"][2].__setitem__("prev_hash", "f" * 64))
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_broken_seq"], data["steps"][2]["seq"])

    def test_tampered_chain_head_is_rejected(self):
        data = self._receipt()
        data["chain_head"] = "0" * 64
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertIn("chain_head", " ".join(result["errors"]))

    def test_tampered_ledger_payload_is_located_by_seq(self):
        executed = [step for step in self.steps if step["kind"] == "executed"][0]
        self.conn.execute(
            "UPDATE run_steps SET payload = ? WHERE run_id = ? AND seq = ?",
            ('{"tampered":true}', self.run["id"], executed["seq"]),
        )
        self.conn.commit()
        result = receipt.verify_against_ledger(self.conn, self.run["id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_broken_seq"], executed["seq"])
        # The exported receipt itself is still internally consistent.
        self.assertTrue(receipt.verify_receipt(receipt.build_receipt(self.conn, self.run["id"]))["ok"])


    def test_tampered_last_step_hash_points_at_that_step(self):
        data = self._receipt()
        last = data["steps"][-1]
        last["hash"] = "b" * 64
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_broken_seq"], last["seq"])
        self.assertIn("step {}".format(last["seq"]), " ".join(result["errors"]))

    def test_malformed_hash_is_located_at_its_step(self):
        data = self._tampered(lambda item: item["steps"][1].__setitem__("hash", "zz" * 8))
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_broken_seq"], data["steps"][1]["seq"])
        self.assertIn("hash is not a sha256", " ".join(result["errors"]))

    def test_missing_field_does_not_cascade_prev_hash_errors(self):
        data = self._tampered(lambda item: item["steps"][1].pop("kind"))
        result = receipt.verify_receipt(data)
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_broken_seq"], data["steps"][1]["seq"])
        cascaded = [
            error for error in result["errors"] if "prev_hash mismatch" in error
        ]
        self.assertEqual(cascaded, [])

class ReceiptVerifyCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "daythread.db")
        init_db(self.conn)
        self.run = runtime.propose(
            self.conn,
            self.home,
            "note.write",
            {"path": "launch.md", "content": "hello"},
            intent="verify cli",
        )
        self.receipt_path = self.home / "receipt.json"
        self.conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_receipt(self):
        conn = connect(self.home / "daythread.db")
        try:
            data = receipt.build_receipt(conn, self.run["id"])
        finally:
            conn.close()
        self.receipt_path.write_text(receipt.to_json(data) + "\n", encoding="utf-8")
        return data

    def test_cli_verify_clean_run(self):
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", self.run["id"], "--verify"]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("verified: true", out)

    def test_cli_verify_input_file(self):
        self._write_receipt()
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", "--input", str(self.receipt_path), "--verify"]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("verified: true", out)

    def test_cli_verify_input_file_tampered_prev_hash(self):
        data = self._write_receipt()
        data["steps"][2]["prev_hash"] = "f" * 64
        self.receipt_path.write_text(receipt.to_json(data) + "\n", encoding="utf-8")
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", "--input", str(self.receipt_path), "--verify"]
        )
        self.assertEqual(code, 1)
        self.assertIn("verified: false", out)
        self.assertIn("first broken step: {}".format(data["steps"][2]["seq"]), out + err)
        self.assertNotIn("Traceback", err)

    def test_cli_verify_input_file_missing_field(self):
        data = self._write_receipt()
        data.pop("digest")
        self.receipt_path.write_text(receipt.to_json(data) + "\n", encoding="utf-8")
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", "--input", str(self.receipt_path), "--verify"]
        )
        self.assertEqual(code, 1)
        self.assertIn("verified: false", out)
        self.assertNotIn("Traceback", err)

    def test_cli_verify_version_mismatch(self):
        data = self._write_receipt()
        data["version"] = 99
        self.receipt_path.write_text(receipt.to_json(data) + "\n", encoding="utf-8")
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", "--input", str(self.receipt_path), "--verify"]
        )
        self.assertEqual(code, 1)
        self.assertIn("version", (out + err).lower())
        self.assertNotIn("Traceback", err)

    def test_cli_verify_rejects_out_and_json(self):
        out_path = self.home / "out.json"
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", self.run["id"], "--verify", "--out", str(out_path)]
        )
        self.assertEqual(code, 1)
        self.assertIn("cannot be combined", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(out_path.exists())

    def test_cli_verify_missing_input_file(self):
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", "--input", str(self.home / "nope.json"), "--verify"]
        )
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        self.assertNotIn("Traceback", err)

    def test_cli_verify_tampered_ledger_exits_nonzero(self):
        conn = connect(self.home / "daythread.db")
        try:
            step = [
                item
                for item in get_steps(conn, self.run["id"])
                if item["kind"] == "executed"
            ][0]
            conn.execute(
                "UPDATE run_steps SET payload = ? WHERE run_id = ? AND seq = ?",
                ('{"tampered":true}', self.run["id"], step["seq"]),
            )
            conn.commit()
        finally:
            conn.close()
        code, out, err = run_cli(
            ["--home", str(self.home), "receipt", self.run["id"], "--verify"]
        )
        self.assertEqual(code, 1)
        self.assertIn("verified: false", out)
        self.assertIn("first broken step: {}".format(step["seq"]), out + err)
        self.assertNotIn("Traceback", err)
