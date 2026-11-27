"""Subprocess entry point for isolated tools."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except Exception:  # noqa: BLE001 - coverage is optional
        pass

from .db import connect, init_db
from .tools import ToolContext, get_tool


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="daythread-tool")
    parser.add_argument("--db", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--args", default="{}")
    parser.add_argument("--idempotency-key", default=None)
    args = parser.parse_args(argv)

    conn = None
    try:
        conn = connect(Path(args.db))
        init_db(conn)
        context = ToolContext(
            conn=conn,
            home=Path(args.home),
            run_id=args.run_id,
            idempotency_key=args.idempotency_key,
            db_path=Path(args.db),
        )
        spec = get_tool(args.tool)
        result = spec.handler(context, json.loads(args.args))
        payload = {
            "ok": True,
            "result": {
                "summary": result.summary,
                "undo": result.undo,
                "irreversible": result.irreversible,
                "data": result.data,
            },
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - serialized to the parent
        print(
            json.dumps(
                {"ok": False, "error": "{}: {}".format(type(exc).__name__, exc)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
