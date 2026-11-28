import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from daythread import bench
from daythread.cli import main
from daythread.db import connect, init_db


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class BenchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_bench_run(self):
        results = bench.run(self.conn, self.home, facts=10, runs=2)
        self.assertEqual(results["facts"], 10)
        self.assertEqual(results["runs"], 2)
        self.assertTrue(results["safety_pass"])
        self.assertIn("memory_search_ms_mean", results)
        self.assertIn("runtime_plan_ms", results)

    def test_bench_cli(self):
        code, out, _ = run_cli(
            ["--home", str(self.home), "--json", "bench", "--facts", "5", "--runs", "1"]
        )
        self.assertEqual(code, 0)
        results = json.loads(out)
        self.assertTrue(results["safety_pass"])


    def test_bench_safety_cases_shape(self):
        results = bench.run(self.conn, self.home, facts=5, runs=1)
        cases = results["safety_cases"]
        self.assertEqual(
            [case["name"] for case in cases],
            ["receipt_tamper_rejected", "undo_restores_baseline"],
        )
        for case in cases:
            self.assertTrue(case["pass"])
            self.assertIsInstance(case["ms"], (int, float))
            self.assertGreaterEqual(case["ms"], 0)
        self.assertTrue(results["safety_receipt_tamper_rejected"])
        self.assertTrue(results["safety_undo_restores_baseline"])

    def test_bench_cli_json_includes_safety_cases(self):
        code, out, _ = run_cli(
            ["--home", str(self.home), "--json", "bench", "--facts", "5", "--runs", "1"]
        )
        self.assertEqual(code, 0)
        results = json.loads(out)
        cases = results["safety_cases"]
        self.assertEqual(len(cases), 2)
        for case in cases:
            self.assertTrue(case["pass"])
            self.assertIn("ms", case)

    def test_bench_is_stable_across_ten_cli_runs(self):
        signatures = []
        for _ in range(10):
            code, out, _ = run_cli(
                [
                    "--home",
                    str(self.home),
                    "--json",
                    "bench",
                    "--facts",
                    "3",
                    "--runs",
                    "1",
                ]
            )
            self.assertEqual(code, 0)
            results = json.loads(out)
            signatures.append(
                [(case["name"], case["pass"]) for case in results["safety_cases"]]
            )
        self.assertEqual(len(set(tuple(signature) for signature in signatures)), 1)
        self.assertTrue(all(passed for signature in signatures for _, passed in signature))

    def test_bench_failure_returns_nonzero(self):
        fake = {
            "safety_pass": False,
            "safety_cases": [
                {"name": "receipt_tamper_rejected", "pass": False, "ms": 0.1}
            ],
        }
        with mock.patch("daythread.bench.run", return_value=fake):
            code, out, err = run_cli(["--home", str(self.home), "--json", "bench"])
        self.assertNotEqual(code, 0)
        self.assertIn("error:", err)
        self.assertIn("bench safety failed", err)
        self.assertIn("receipt_tamper_rejected", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
