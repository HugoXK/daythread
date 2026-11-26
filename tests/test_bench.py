import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
