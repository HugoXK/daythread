"""Local load smoke: memory, plans, and concurrent writers."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daythread import db, memory, runtime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", type=int, default=2000)
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--per-thread", type=int, default=25)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        db_path = home / "daythread.db"
        conn = db.connect(db_path)
        db.init_db(conn)

        started = time.perf_counter()
        for index in range(max(args.facts, 0)):
            memory.add_fact(
                conn, "person", "person-{}".format(index), "role", "engineer", source="load"
            )
        insert_seconds = time.perf_counter() - started

        started = time.perf_counter()
        for index in range(max(args.runs, 0)):
            runtime.propose(
                conn,
                home,
                "note.write",
                {"path": "load/{}.md".format(index), "content": "x"},
                intent="load",
            )
        run_seconds = time.perf_counter() - started

        concurrency = {"threads": args.threads, "success": 0, "errors": []}
        if args.threads > 0 and args.per_thread > 0:
            lock = threading.Lock()

            def worker(worker_id: int) -> None:
                local = db.connect(db_path)
                local_ok = 0
                local_errors = []
                try:
                    for index in range(args.per_thread):
                        try:
                            runtime.propose(
                                local,
                                home,
                                "note.write",
                                {
                                    "path": "concurrent/{}-{}.md".format(worker_id, index),
                                    "content": "x",
                                },
                                intent="load-concurrent",
                            )
                            local_ok += 1
                        except Exception as exc:  # noqa: BLE001 - load report
                            local_errors.append("{}: {}".format(type(exc).__name__, exc))
                finally:
                    local.close()
                with lock:
                    concurrency["success"] += local_ok
                    concurrency["errors"].extend(local_errors[:2])

            threads = [
                threading.Thread(target=worker, args=(index,))
                for index in range(args.threads)
            ]
            started = time.perf_counter()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            concurrency["seconds"] = round(time.perf_counter() - started, 3)

        conn.execute("PRAGMA optimize")
        conn.close()

        report = {
            "facts": args.facts,
            "facts_insert_seconds": round(insert_seconds, 3),
            "facts_per_second": round(args.facts / max(insert_seconds, 1e-9), 1),
            "runs": args.runs,
            "runs_seconds": round(run_seconds, 3),
            "runs_per_second": round(args.runs / max(run_seconds, 1e-9), 1),
            "concurrency": concurrency,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
