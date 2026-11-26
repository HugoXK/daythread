"""`dt` command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, brief, export, memory, policy, runtime
from .db import connect, init_db, is_initialized
from .ledger import get_steps, verify_run

SUBJECT_TYPES = ("self", "person", "project", "org", "routine", "goal")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dt",
        description="Daythread: local-first, auditable personal agent.",
    )
    parser.add_argument(
        "--home",
        default=os.environ.get("DAYTHREAD_HOME", str(Path.home() / ".daythread")),
        help="data directory (default: $DAYTHREAD_HOME or ~/.daythread)",
    )
    parser.add_argument("--db", default=None, help="override the SQLite database path")
    parser.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    parser.add_argument("--version", action="version", version="daythread {}".format(__version__))

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="initialize the local database")

    sub.add_parser("demo", help="seed a small demo dataset and print a brief")

    fact = sub.add_parser("fact", help="manage structured memory facts")
    fact_sub = fact.add_subparsers(dest="fact_cmd", required=True)

    fact_add = fact_sub.add_parser("add", help="add a fact")
    fact_add.add_argument("--type", required=True, choices=SUBJECT_TYPES)
    fact_add.add_argument("--subject", required=True)
    fact_add.add_argument("--predicate", required=True)
    fact_add.add_argument("--value", required=True)
    fact_add.add_argument("--confidence", type=float, default=0.8)
    fact_add.add_argument("--source", default="user")

    fact_list = fact_sub.add_parser("list", help="list facts")
    fact_list.add_argument("--type", choices=SUBJECT_TYPES)
    fact_list.add_argument("--subject")

    fact_show = fact_sub.add_parser("show", help="show one fact")
    fact_show.add_argument("id")

    fact_rm = fact_sub.add_parser("rm", help="tombstone a fact")
    fact_rm.add_argument("id")

    fact_search = fact_sub.add_parser("search", help="search facts")
    fact_search.add_argument("query")

    task = sub.add_parser("task", help="inspect local tasks")
    task_sub = task.add_subparsers(dest="task_cmd", required=True)
    task_sub.add_parser("list", help="list tasks")
    task_done = task_sub.add_parser("done", help="mark a task done")
    task_done.add_argument("id")

    brief_p = sub.add_parser("brief", help="print the morning or evening brief")
    brief_p.add_argument("--evening", action="store_true")

    act = sub.add_parser("act", help="propose a tool action (runs the policy gate)")
    act.add_argument("tool", help="tool name, e.g. note.write or message.send")
    act.add_argument("--intent", default=None)
    act.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="tool argument (repeatable)",
    )

    runs = sub.add_parser("runs", help="list recent runs")
    runs.add_argument("--limit", type=int, default=20)

    show = sub.add_parser("show", help="show a run and its ledger steps")
    show.add_argument("run_id")

    approve = sub.add_parser("approve", help="approve a pending run")
    approve.add_argument("run_id")

    reject = sub.add_parser("reject", help="reject a pending run")
    reject.add_argument("run_id")

    undo = sub.add_parser("undo", help="undo a completed reversible run")
    undo.add_argument("run_id")

    ledger = sub.add_parser("ledger", help="show or verify a run ledger")
    ledger.add_argument("run_id")
    ledger.add_argument("--verify", action="store_true")

    pol = sub.add_parser("policy", help="show or change risk policy")
    pol_sub = pol.add_subparsers(dest="policy_cmd", required=True)
    pol_sub.add_parser("show", help="show the current policy")
    pol_set = pol_sub.add_parser("set", help="set a policy decision")
    pol_set.add_argument("risk", choices=policy.RISKS)
    pol_set.add_argument("decision", choices=policy.DECISIONS)

    exp = sub.add_parser("export", help="export memory and local state")
    exp.add_argument("--format", choices=("md", "json"), default="md")
    exp.add_argument("--out", default=None, help="write to a file instead of stdout")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db).expanduser().resolve() if args.db else home / "daythread.db"
    conn = connect(db_path)
    try:
        if not is_initialized(conn):
            init_db(conn)
        _dispatch(args, conn, home, db_path)
    except (KeyError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


def _dispatch(args: argparse.Namespace, conn, home: Path, db_path: Path) -> None:
    cmd = args.cmd
    if cmd == "init":
        print("initialized {}".format(db_path))
    elif cmd == "demo":
        _demo(conn, home, args)
    elif cmd == "fact":
        _fact(args, conn)
    elif cmd == "task":
        _task(args, conn, home)
    elif cmd == "brief":
        data = brief.evening_brief(conn) if args.evening else brief.morning_brief(conn)
        if args.as_json:
            _print_json(data)
        else:
            print(brief.render(data))
    elif cmd == "act":
        payload = _parse_kv(args.arg)
        run = runtime.propose(conn, home, args.tool, payload, intent=args.intent)
        _print_run(run, args.as_json)
    elif cmd == "runs":
        runs = runtime.list_runs(conn, limit=args.limit)
        if args.as_json:
            _print_json(runs)
        else:
            if not runs:
                print("no runs yet")
            for run in runs:
                print(
                    "{}  {:<18} {:<10} risk={:<11} {}".format(
                        run["id"][:8], run["state"], run["tool"], run["risk"], run["intent"]
                    )
                )
    elif cmd == "show":
        _print_run(runtime.get_run(conn, args.run_id), args.as_json, steps=True)
    elif cmd == "approve":
        _print_run(runtime.approve(conn, home, args.run_id), args.as_json, steps=True)
    elif cmd == "reject":
        _print_run(runtime.reject(conn, args.run_id), args.as_json, steps=True)
    elif cmd == "undo":
        _print_run(runtime.undo_run(conn, home, args.run_id), args.as_json, steps=True)
    elif cmd == "ledger":
        _ledger(args, conn)
    elif cmd == "policy":
        _policy(args, conn)
    elif cmd == "export":
        _export(args, conn)
    else:
        raise ValueError("unknown command: {}".format(cmd))


def _fact(args: argparse.Namespace, conn) -> None:
    if args.fact_cmd == "add":
        fact = memory.add_fact(
            conn,
            subject_type=args.type,
            subject_id=args.subject,
            predicate=args.predicate,
            value=args.value,
            confidence=args.confidence,
            source=args.source,
        )
        if args.as_json:
            _print_json(fact)
        else:
            print(
                "remembered {} · {} · {}  ({})".format(
                    fact["subject_id"], fact["predicate"], fact["object"], fact["id"][:8]
                )
            )
    elif args.fact_cmd == "list":
        facts = memory.list_facts(conn, subject_type=args.type, subject_id=args.subject)
        if args.as_json:
            _print_json(facts)
        else:
            _print_facts(facts)
    elif args.fact_cmd == "show":
        fact = memory.get_fact(conn, args.id)
        if args.as_json:
            _print_json(fact)
        else:
            _print_facts([fact])
    elif args.fact_cmd == "rm":
        fact = memory.delete_fact(conn, args.id)
        if args.as_json:
            _print_json(fact)
        else:
            print("deleted {} (tombstoned)".format(fact["id"][:8]))
    elif args.fact_cmd == "search":
        facts = memory.search_facts(conn, args.query)
        if args.as_json:
            _print_json(facts)
        else:
            _print_facts(facts)


def _task(args: argparse.Namespace, conn, home: Path) -> None:
    if args.task_cmd == "list":
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY status, due"
        ).fetchall()
        tasks = [dict(row) for row in rows]
        if args.as_json:
            _print_json(tasks)
        else:
            if not tasks:
                print("no tasks")
            for task in tasks:
                due = " (due {})".format(task["due"]) if task["due"] else ""
                print("[{}] {}  {}{}".format(task["status"], task["id"][:8], task["title"], due))
    elif args.task_cmd == "done":
        run = runtime.propose(
            conn,
            home,
            "task.done",
            {"id": args.id},
            intent="complete task {}".format(args.id[:8]),
        )
        _print_run(run, args.as_json)


def _ledger(args: argparse.Namespace, conn) -> None:
    steps = get_steps(conn, args.run_id)
    ok = None
    errors: List[str] = []
    if args.verify:
        ok, errors = verify_run(conn, args.run_id)
        if args.as_json:
            _print_json({"run_id": args.run_id, "ok": ok, "errors": errors, "steps": steps})
            return
        print("ledger {}: {}".format("OK" if ok else "TAMPERED", args.run_id))
        for error in errors:
            print("  - {}".format(error))
        print("")
    if args.as_json:
        _print_json(steps)
        return
    if not steps:
        print("no steps for run {}".format(args.run_id))
    for step in steps:
        print(
            "{:>3}  {:<18} {:<12} {}".format(
                step["seq"],
                step["kind"],
                step["hash"][:12],
                json.dumps(step["payload"], ensure_ascii=False, sort_keys=True),
            )
        )


def _policy(args: argparse.Namespace, conn) -> None:
    if args.policy_cmd == "show":
        current = policy.load_policy(conn)
        if args.as_json:
            _print_json(current)
        else:
            for risk in policy.RISKS:
                print("{:<12} {}".format(risk, current[risk]))
    elif args.policy_cmd == "set":
        policy.set_policy(conn, args.risk, args.decision)
        print("policy {} = {}".format(args.risk, args.decision))


def _export(args: argparse.Namespace, conn) -> None:
    if args.format == "json":
        text = json.dumps(export.export_json(conn), ensure_ascii=False, indent=2)
    else:
        text = export.export_markdown(conn)
    if args.out:
        path = Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print("exported to {}".format(path))
    else:
        print(text)


def _demo(conn, home: Path, args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today = now.date().isoformat()

    facts = [
        ("self", "hugo", "timezone", "Asia/Shanghai"),
        ("person", "maya", "role", "co-founder"),
        ("person", "maya", "prefers", "async updates"),
        ("project", "daythread", "stage", "baseline"),
        ("self", "hugo", "commitment", "send the deck to Maya by Friday"),
    ]
    for subject_type, subject_id, predicate, value in facts:
        memory.add_fact(conn, subject_type, subject_id, predicate, value)

    runtime.propose(
        conn,
        home,
        "calendar.add",
        {
            "title": "Design review",
            "start": (now + timedelta(hours=2)).isoformat(),
        },
        intent="demo calendar",
    )
    runtime.propose(
        conn,
        home,
        "task.create",
        {"title": "Send investor update", "due": today},
        intent="demo task",
    )
    draft = runtime.propose(
        conn,
        home,
        "message.draft",
        {
            "to": "maya@example.com",
            "subject": "Deck",
            "body": "Latest version attached.",
        },
        intent="demo draft",
    )
    external = runtime.propose(
        conn,
        home,
        "message.send",
        {
            "to": "maya@example.com",
            "subject": "Deck",
            "body": "Latest version attached.",
        },
        intent="demo external send",
    )

    data = brief.morning_brief(conn, now=now)
    if args.as_json:
        _print_json(
            {
                "brief": data,
                "draft_run": draft["id"],
                "pending_run": external["id"],
            }
        )
        return
    print(brief.render(data))
    print("")
    print("Draft run:   {} ({})".format(draft["id"][:8], draft["state"]))
    print("External run: {} ({})".format(external["id"][:8], external["state"]))
    print("Approve with: dt approve {}".format(external["id"]))


def _parse_kv(values: List[str]) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise ValueError("--arg must be KEY=VALUE, got: {}".format(item))
        key, value = item.split("=", 1)
        if not key:
            raise ValueError("--arg key must not be empty")
        parsed[key] = value
    return parsed


def _print_run(run: Dict[str, Any], as_json: bool, steps: bool = False) -> None:
    if as_json:
        _print_json(run)
        return
    print(
        "run {}  [{}]  tool={}  risk={}  decision={}".format(
            run["id"][:8], run["state"], run["tool"], run["risk"], run["decision"]
        )
    )
    print("  intent: {}".format(run["intent"]))
    if run["state"] == "pending_approval":
        print("  next:   dt approve {}".format(run["id"]))
    if run["state"] in ("completed", "undone", "failed", "rejected", "denied"):
        print("  replay: dt ledger {}".format(run["id"]))
    if steps:
        for step in run.get("steps", []):
            print(
                "  [{:>2}] {:<16} {}".format(
                    step["seq"], step["kind"], json.dumps(step["payload"], ensure_ascii=False)
                )
            )


def _print_facts(facts: List[Dict[str, Any]]) -> None:
    if not facts:
        print("no facts")
        return
    for fact in facts:
        deleted = " (deleted)" if fact.get("deleted_at") else ""
        print(
            "{}  {:<9} {} · {} · {}{}".format(
                fact["id"][:8],
                fact["subject_type"],
                fact["subject_id"],
                fact["predicate"],
                fact["object"],
                deleted,
            )
        )


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
