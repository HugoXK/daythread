"""`dt` command line interface."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import (
    __version__,
    backup,
    bench,
    brief,
    connectors,
    crypto,
    export,
    mcp,
    memory,
    migrations,
    models,
    oauth,
    obs,
    planner,
    policy,
    proactivity,
    runtime,
    scheduler,
    skills,
    sync,
)
from .crypto import SecretVault
from .db import connect, init_db, is_initialized, set_setting
from .errors import DaythreadError
from .ledger import get_steps, verify_run
from .server import ApiServer, ensure_token

SUBJECT_TYPES = memory.SUBJECT_TYPES


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
    sub.add_parser("migrate", help="apply pending schema migrations")
    sub.add_parser("doctor", help="check local state and integrity")
    sub.add_parser("demo", help="seed a small demo dataset and print a brief")

    config = sub.add_parser("config", help="show or set local settings")
    config_sub = config.add_subparsers(dest="config_cmd", required=True)
    config_sub.add_parser("show", help="show settings")
    config_set = config_sub.add_parser("set", help="set a setting")
    config_set.add_argument("key")
    config_set.add_argument("value")

    fact = sub.add_parser("fact", help="manage structured memory facts")
    fact_sub = fact.add_subparsers(dest="fact_cmd", required=True)

    fact_add = fact_sub.add_parser("add", help="add a fact")
    fact_add.add_argument("--type", required=True, choices=SUBJECT_TYPES)
    fact_add.add_argument("--subject", required=True)
    fact_add.add_argument("--predicate", required=True)
    fact_add.add_argument("--value", required=True)
    fact_add.add_argument("--confidence", type=float, default=0.8)
    fact_add.add_argument("--source", default="user")
    fact_add.add_argument("--tags", default="")

    fact_list = fact_sub.add_parser("list", help="list facts")
    fact_list.add_argument("--type", choices=SUBJECT_TYPES)
    fact_list.add_argument("--subject")

    fact_show = fact_sub.add_parser("show", help="show one fact")
    fact_show.add_argument("id")

    fact_rm = fact_sub.add_parser("rm", help="tombstone a fact")
    fact_rm.add_argument("id")

    fact_search = fact_sub.add_parser("search", help="search facts")
    fact_search.add_argument("query")

    fact_edit = fact_sub.add_parser("edit", help="edit a fact")
    fact_edit.add_argument("id")
    fact_edit.add_argument("--value")
    fact_edit.add_argument("--confidence", type=float)
    fact_edit.add_argument("--tags")
    fact_edit.add_argument("--sensitivity", choices=memory.SENSITIVITIES)

    fact_extract = fact_sub.add_parser("extract", help="extract candidate facts from text")
    fact_extract.add_argument("text")
    fact_extract.add_argument("--apply", action="store_true")

    fact_history = fact_sub.add_parser("history", help="show fact history")
    fact_history.add_argument("id")

    fact_sub.add_parser("conflicts", help="list conflicting facts")

    fact_import = fact_sub.add_parser("import", help="import facts from JSON")
    fact_import.add_argument("path")

    task = sub.add_parser("task", help="manage local tasks")
    task_sub = task.add_subparsers(dest="task_cmd", required=True)
    task_sub.add_parser("list", help="list tasks")
    task_done = task_sub.add_parser("done", help="mark a task done")
    task_done.add_argument("id")
    task_create = task_sub.add_parser("create", help="create a task")
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--due", default=None)
    task_update = task_sub.add_parser("update", help="update a task")
    task_update.add_argument("id")
    task_update.add_argument("--title")
    task_update.add_argument("--status")
    task_update.add_argument("--due")

    brief_p = sub.add_parser("brief", help="print the morning or evening brief")
    brief_p.add_argument("--evening", action="store_true")

    plan = sub.add_parser("plan", help="preview or execute a plan")
    plan.add_argument("intent", nargs="?", help="intent template, e.g. 'send me@x | Deck | hi'")
    plan.add_argument("--file", default=None, help="JSON plan file with steps")
    plan.add_argument("--execute", action="store_true")
    plan.add_argument("--idempotency-key", default=None)

    act = sub.add_parser("act", help="propose a tool action (runs the policy gate)")
    act.add_argument("tool", help="tool name, e.g. note.write or message.send")
    act.add_argument("--intent", default=None)
    act.add_argument("--idempotency-key", default=None)
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

    for name, help_text in (
        ("approve", "approve a pending run"),
        ("reject", "reject a pending run"),
        ("undo", "undo a completed reversible run"),
        ("resume", "resume a failed or paused run"),
        ("cancel", "cancel a pending or running run"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("run_id")

    ledger = sub.add_parser("ledger", help="show or verify a run ledger")
    ledger.add_argument("run_id")
    ledger.add_argument("--verify", action="store_true")

    pol = sub.add_parser("policy", help="show or change risk policy")
    pol_sub = pol.add_subparsers(dest="policy_cmd", required=True)
    pol_sub.add_parser("show", help="show the current policy")
    pol_set = pol_sub.add_parser("set", help="set a policy decision")
    pol_set.add_argument("risk", choices=policy.RISKS)
    pol_set.add_argument("decision", choices=policy.DECISIONS)

    signal = sub.add_parser("signal", help="ingest and list proactivity signals")
    signal_sub = signal.add_subparsers(dest="signal_cmd", required=True)
    signal_add = signal_sub.add_parser("add", help="add a signal")
    signal_add.add_argument("--source", required=True)
    signal_add.add_argument("--kind", required=True)
    signal_add.add_argument("--payload", default="{}")
    signal_add.add_argument("--dedupe-key", default=None)
    signal_list = signal_sub.add_parser("list", help="list signals")
    signal_list.add_argument("--unprocessed", action="store_true")
    signal_list.add_argument("--limit", type=int, default=50)

    proactive = sub.add_parser("proactive", help="score candidates and enforce the interrupt budget")
    proactive.add_argument("--deliver", action="store_true")

    candidate = sub.add_parser("candidate", help="inspect proactive candidates")
    candidate_sub = candidate.add_subparsers(dest="candidate_cmd", required=True)
    candidate_list = candidate_sub.add_parser("list", help="list candidates")
    candidate_list.add_argument("--status", default="new")
    candidate_list.add_argument("--limit", type=int, default=20)

    feedback = sub.add_parser("feedback", help="record proactive feedback")
    feedback.add_argument("target")
    feedback.add_argument("action", choices=("accept", "ignore", "mute"))

    connector = sub.add_parser("connector", help="manage connectors")
    connector_sub = connector.add_subparsers(dest="connector_cmd", required=True)
    connector_sub.add_parser("list", help="list connectors")
    connector_auth = connector_sub.add_parser("auth", help="authorize an OAuth connector")
    connector_auth.add_argument("id")
    connector_auth.add_argument("--no-browser", action="store_true")
    connector_auth.add_argument("--timeout", type=float, default=300.0)
    connector_connect = connector_sub.add_parser("connect", help="connect a connector")
    connector_connect.add_argument("id")
    connector_connect.add_argument("--token-ref", default=None)
    connector_disconnect = connector_sub.add_parser("disconnect", help="disconnect a connector")
    connector_disconnect.add_argument("id")
    connector_sync = connector_sub.add_parser("sync", help="sync a connected connector")
    connector_sync.add_argument("id")
    connector_items = connector_sub.add_parser("items", help="list synced items")
    connector_items.add_argument("id")
    connector_items.add_argument("--kind", default=None)

    skill = sub.add_parser("skill", help="manage declarative skills")
    skill_sub = skill.add_subparsers(dest="skill_cmd", required=True)
    skill_sub.add_parser("list", help="list skills")
    skill_show = skill_sub.add_parser("show", help="show a skill manifest")
    skill_show.add_argument("id")
    skill_install = skill_sub.add_parser("install", help="install a skill from JSON")
    skill_install.add_argument("path")
    skill_run = skill_sub.add_parser("run", help="run a skill as a plan")
    skill_run.add_argument("id")
    skill_run.add_argument("--idempotency-key", default=None)
    skill_uninstall = skill_sub.add_parser("uninstall", help="uninstall a skill")
    skill_uninstall.add_argument("id")

    sub.add_parser("metrics", help="show local metrics")
    audit = sub.add_parser("audit", help="show the audit log")
    audit.add_argument("--limit", type=int, default=50)

    exp = sub.add_parser("export", help="export memory and local state")
    exp.add_argument("--format", choices=("md", "json"), default="md")
    exp.add_argument("--out", default=None, help="write to a file instead of stdout")

    serve = sub.add_parser("serve", help="start the local HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    ask = sub.add_parser("ask", help="ask the configured model provider")
    ask.add_argument("text")
    ask.add_argument("--task", default="chat")
    ask.add_argument("--no-cache", action="store_true")

    model = sub.add_parser("model", help="model gateway configuration and usage")
    model_sub = model.add_subparsers(dest="model_cmd", required=True)
    model_sub.add_parser("list", help="show model config")
    model_usage = model_sub.add_parser("usage", help="show recent model usage")
    model_usage.add_argument("--limit", type=int, default=20)

    mcp_p = sub.add_parser("mcp", help="manage MCP stdio servers")
    mcp_sub = mcp_p.add_subparsers(dest="mcp_cmd", required=True)
    mcp_add = mcp_sub.add_parser("add", help="register an MCP server")
    mcp_add.add_argument("id")
    mcp_add.add_argument("--command", required=True, help="command line, e.g. 'python3 server.py'")
    mcp_add.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    mcp_sub.add_parser("list", help="list MCP servers")
    mcp_remove = mcp_sub.add_parser("remove", help="remove an MCP server")
    mcp_remove.add_argument("id")
    mcp_tools = mcp_sub.add_parser("tools", help="list tools exposed by a server")
    mcp_tools.add_argument("id")
    mcp_call = mcp_sub.add_parser("call", help="propose an MCP tool call")
    mcp_call.add_argument("id")
    mcp_call.add_argument("tool")
    mcp_call.add_argument("--intent", default=None)
    mcp_call.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE")

    tick = sub.add_parser("tick", help="run one scheduler job")
    tick.add_argument("--job", choices=scheduler.JOBS, default=None)

    daemon = sub.add_parser("daemon", help="run the scheduler loop")
    daemon.add_argument("--interval", type=int, default=60)
    daemon.add_argument("--max-ticks", type=int, default=None)

    notifications = sub.add_parser("notifications", help="list scheduler notifications")
    notifications.add_argument("--limit", type=int, default=50)

    sched = sub.add_parser("scheduler", help="inspect scheduler runs")
    sched_sub = sched.add_subparsers(dest="scheduler_cmd", required=True)
    sched_list = sched_sub.add_parser("list", help="list scheduler runs")
    sched_list.add_argument("--limit", type=int, default=20)

    backup_p = sub.add_parser("backup", help="create a checksummed local backup")
    backup_p.add_argument("--out", default=None)

    bench_p = sub.add_parser("bench", help="run Daythread Bench v0 in a temp home")
    bench_p.add_argument("--facts", type=int, default=300)
    bench_p.add_argument("--runs", type=int, default=20)

    restore = sub.add_parser("restore", help="restore or verify a backup")
    restore.add_argument("path")
    restore.add_argument("--replace", action="store_true")
    restore.add_argument("--verify", action="store_true")


    secret = sub.add_parser("secret", help="encrypted local secret vault")
    secret_sub = secret.add_subparsers(dest="secret_cmd", required=True)
    secret_sub.add_parser("list", help="list secret names")
    secret_set = secret_sub.add_parser("set", help="set a secret")
    secret_set.add_argument("name")
    secret_set.add_argument("--value", default=None, help="insecure: prefer stdin or a TTY prompt")
    secret_rm = secret_sub.add_parser("rm", help="delete a secret")
    secret_rm.add_argument("name")

    sync_p = sub.add_parser("sync", help="passphrase-encrypted file sync")
    sync_sub = sync_p.add_subparsers(dest="sync_cmd", required=True)
    sync_export = sync_sub.add_parser("export", help="export an encrypted sync bundle")
    sync_export.add_argument("--out", default=None)
    sync_import = sync_sub.add_parser("import", help="import an encrypted sync bundle")
    sync_import.add_argument("path")
    sync_import.add_argument("--replace", action="store_true")
    sync_verify = sync_sub.add_parser("verify", help="verify an encrypted sync bundle")
    sync_verify.add_argument("path")
    for command in (sync_export, sync_import, sync_verify):
        command.add_argument("--passphrase", default=None)
        command.add_argument("--passphrase-env", default="DAYTHREAD_PASSPHRASE")

    serve.add_argument("--print-token", action="store_true")
    serve.add_argument("--rotate-token", action="store_true")

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
    except (DaythreadError, KeyError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


def _dispatch(args: argparse.Namespace, conn, home: Path, db_path: Path) -> None:
    cmd = args.cmd
    if cmd == "init":
        print("initialized {} (schema v{})".format(db_path, migrations.current_version(conn)))
    elif cmd == "migrate":
        version = migrations.migrate(conn)
        print("schema v{}".format(version))
    elif cmd == "doctor":
        _doctor(conn, home, db_path)
    elif cmd == "config":
        _config(args, conn)
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
    elif cmd == "plan":
        _plan(args, conn, home)
    elif cmd == "act":
        payload = _parse_kv(args.arg)
        run = runtime.propose(
            conn,
            home,
            args.tool,
            payload,
            intent=args.intent,
            idempotency_key=args.idempotency_key,
        )
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
        _print_run(runtime.get_run(conn, args.run_id), args.as_json, steps=True, verbose=True)
    elif cmd == "approve":
        _print_run(runtime.approve(conn, home, args.run_id), args.as_json, steps=True)
    elif cmd == "reject":
        _print_run(runtime.reject(conn, args.run_id), args.as_json, steps=True)
    elif cmd == "undo":
        _print_run(runtime.undo_run(conn, home, args.run_id), args.as_json, steps=True)
    elif cmd == "resume":
        _print_run(runtime.resume_run(conn, home, args.run_id), args.as_json, steps=True)
    elif cmd == "cancel":
        _print_run(runtime.cancel_run(conn, args.run_id), args.as_json, steps=True)
    elif cmd == "ledger":
        _ledger(args, conn)
    elif cmd == "policy":
        _policy(args, conn)
    elif cmd == "signal":
        _signal(args, conn)
    elif cmd == "proactive":
        _proactive(args, conn)
    elif cmd == "candidate":
        _candidate(args, conn)
    elif cmd == "feedback":
        result = proactivity.feedback(conn, args.target, args.action)
        _print_json(result) if args.as_json else print(
            "feedback {}: {}".format(result["action"], result["target"][:8])
        )
    elif cmd == "connector":
        _connector(args, conn, home)
    elif cmd == "skill":
        _skill(args, conn, home)
    elif cmd == "metrics":
        _print_json(obs.snapshot(conn))
    elif cmd == "audit":
        entries = obs.audit_history(conn, limit=args.limit)
        if args.as_json:
            _print_json(entries)
        else:
            for entry in entries:
                print(
                    "{}  {:<10} {:<22} {}".format(
                        entry["ts"], entry["actor"], entry["action"], entry["target"] or ""
                    )
                )
    elif cmd == "export":
        _export(args, conn)
    elif cmd == "ask":
        response = models.ask(
            conn, args.text, task=args.task, use_cache=not args.no_cache
        )
        if args.as_json:
            _print_json(response.to_dict())
        else:
            print(response.text)
    elif cmd == "model":
        _model(args, conn)
    elif cmd == "mcp":
        _mcp(args, conn, home)
    elif cmd == "tick":
        result = scheduler.tick(conn, home, job=args.job)
        _tick_result(result, args.as_json)
    elif cmd == "daemon":
        try:
            scheduler.daemon(home, db_path, interval=args.interval, max_ticks=args.max_ticks)
        except KeyboardInterrupt:
            pass
        print("daemon stopped")
    elif cmd == "notifications":
        items = scheduler.list_notifications(conn, limit=args.limit)
        if args.as_json:
            _print_json(items)
        else:
            for item in items:
                print(
                    "{}  {:<10} {:<10} {}".format(
                        item["ts"], item["status"], item["kind"], item["title"]
                    )
                )
    elif cmd == "scheduler":
        items = scheduler.list_scheduler_runs(conn, limit=args.limit)
        if args.as_json:
            _print_json(items)
        else:
            for item in items:
                print(
                    "{}  {:<12} {:<10} {}".format(
                        item["started_at"], item["job"], item["status"], item["scheduled_for"]
                    )
                )
    elif cmd == "bench":
        _bench(args)
    elif cmd == "backup":
        path = backup.create_backup(
            conn, home, Path(args.out).expanduser() if args.out else None
        )
        if args.as_json:
            _print_json({"path": str(path)})
        else:
            print("backup written to {}".format(path))
    elif cmd == "restore":
        path = Path(args.path).expanduser()
        if args.verify:
            info = backup.verify_backup(path)
            _print_json(info) if args.as_json else print(
                "backup OK: {} ({} fact(s), schema v{})".format(
                    info["created_at"], info["counts"].get("facts", 0), info["schema_version"]
                )
            )
        else:
            counts = backup.restore_backup(conn, path, replace=args.replace)
            _print_json(counts) if args.as_json else print(
                "restored: {}".format(json.dumps(counts, sort_keys=True))
            )
    elif cmd == "secret":
        _secret(args, conn, home)
    elif cmd == "sync":
        _sync(args, conn, home)
    elif cmd == "serve":
        _serve(args, home, db_path)
    else:
        raise ValueError("unknown command: {}".format(cmd))


def _fact(args: argparse.Namespace, conn) -> None:
    if args.fact_cmd == "add":
        tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
        fact = memory.add_fact(
            conn,
            subject_type=args.type,
            subject_id=args.subject,
            predicate=args.predicate,
            value=args.value,
            confidence=args.confidence,
            source=args.source,
            tags=tags,
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
    elif args.fact_cmd == "edit":
        tags = None
        if args.tags is not None:
            tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
        fact = memory.edit_fact(
            conn,
            args.id,
            value=args.value,
            confidence=args.confidence,
            tags=tags,
            sensitivity=args.sensitivity,
        )
        _print_json(fact) if args.as_json else _print_facts([fact])
    elif args.fact_cmd == "extract":
        candidates = memory.extract_facts(args.text)
        if args.apply:
            facts = memory.apply_extracted(conn, candidates)
            if args.as_json:
                _print_json(facts)
            else:
                _print_facts(facts)
        else:
            if args.as_json:
                _print_json(candidates)
            else:
                if not candidates:
                    print("no facts found")
                for candidate in candidates:
                    print(
                        "{:<9} {} · {} · {}  ({:.2f})".format(
                            candidate["subject_type"],
                            candidate["subject_id"],
                            candidate["predicate"],
                            candidate["value"],
                            candidate["confidence"],
                        )
                    )
    elif args.fact_cmd == "history":
        entries = memory.history(conn, args.id)
        if args.as_json:
            _print_json(entries)
        else:
            for entry in entries:
                print("{}  {}".format(entry["ts"], entry["action"]))
    elif args.fact_cmd == "conflicts":
        conflicts = memory.conflicts(conn)
        if args.as_json:
            _print_json(conflicts)
        else:
            if not conflicts:
                print("no conflicts")
            for item in conflicts:
                print(
                    "{} · {} · {}  ({} variants)".format(
                        item["subject_type"], item["subject_id"], item["predicate"], item["variants"]
                    )
                )
    elif args.fact_cmd == "import":
        path = Path(args.path).expanduser()
        data = json.loads(path.read_text(encoding="utf-8"))
        count = memory.import_json(conn, data)
        print("imported {} fact(s)".format(count))


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
            conn, home, "task.done", {"id": args.id}, intent="complete task {}".format(args.id[:8])
        )
        _print_run(run, args.as_json)
    elif args.task_cmd == "create":
        run = runtime.propose(
            conn,
            home,
            "task.create",
            {"title": args.title, "due": args.due},
            intent="create task",
        )
        _print_run(run, args.as_json)
    elif args.task_cmd == "update":
        payload = {"id": args.id}
        if args.title is not None:
            payload["title"] = args.title
        if args.status is not None:
            payload["status"] = args.status
        if args.due is not None:
            payload["due"] = args.due
        run = runtime.propose(conn, home, "task.update", payload, intent="update task")
        _print_run(run, args.as_json)


def _plan(args: argparse.Namespace, conn, home: Path) -> None:
    if args.file:
        data = json.loads(Path(args.file).expanduser().read_text(encoding="utf-8"))
        steps = planner.from_json(data)
        intent = None
    elif args.intent:
        steps = planner.from_intent(args.intent)
        intent = args.intent
    else:
        raise ValueError("plan needs an intent or --file")

    if args.execute:
        run = runtime.propose_plan(
            conn,
            home,
            steps,
            intent=intent,
            idempotency_key=args.idempotency_key,
        )
        _print_run(run, args.as_json, steps=True)
        return
    preview = planner.preview(conn, steps)
    if args.as_json:
        _print_json(preview)
        return
    print(
        "plan: {} step(s)  risk={}  approval={}  denied={}".format(
            len(preview["steps"]),
            preview["risk"],
            preview["requires_approval"],
            preview["denied"],
        )
    )
    for item in preview["steps"]:
        deps = ",".join(item["depends_on"]) or "-"
        print(
            "  {:<4} {:<18} {:<12} {:<6} deps={}".format(
                item["id"], item["tool"], item["risk"], item["decision"], deps
            )
        )


def _ledger(args: argparse.Namespace, conn) -> None:
    steps = get_steps(conn, args.run_id)
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


def _signal(args: argparse.Namespace, conn) -> None:
    if args.signal_cmd == "add":
        try:
            payload = json.loads(args.payload)
        except ValueError:
            raise ValueError("payload must be valid JSON")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        signal = proactivity.ingest_signal(
            conn,
            source=args.source,
            kind=args.kind,
            payload=payload,
            dedupe_key=args.dedupe_key,
        )
        _print_json(signal) if args.as_json else print(
            "signal {} ({}:{})".format(signal["id"][:8], signal["source"], signal["kind"])
        )
    elif args.signal_cmd == "list":
        signals = proactivity.list_signals(conn, unprocessed=args.unprocessed, limit=args.limit)
        if args.as_json:
            _print_json(signals)
        else:
            if not signals:
                print("no signals")
            for signal in signals:
                print(
                    "{}  {:<8} {:<10} {}".format(
                        signal["ts"], signal["source"], signal["kind"], signal["payload"]
                    )
                )


def _proactive(args: argparse.Namespace, conn) -> None:
    result = proactivity.proactive(conn, deliver=args.deliver)
    if args.as_json:
        _print_json(result)
        return
    budget = result["budget"]
    print(
        "budget: {}/{}  can_interrupt={}  deferred={}".format(
            budget["sent"], budget["max_sent"], result["can_interrupt"], result.get("deferred", False)
        )
    )
    if not result["candidates"]:
        print("no candidates")
    for item in result["candidates"]:
        marker = "*" if item in result["selected"] else " "
        print("{} {:.2f}  {:<12} {}".format(marker, item["score"], item["kind"], item["title"]))


def _candidate(args: argparse.Namespace, conn) -> None:
    items = proactivity.list_candidates(conn, status=args.status, limit=args.limit)
    if args.as_json:
        _print_json(items)
    else:
        for item in items:
            print("{:.2f}  {:<12} {:<10} {}".format(item["score"], item["kind"], item["status"], item["title"]))


def _connector(args: argparse.Namespace, conn, home: Path) -> None:
    if args.connector_cmd == "list":
        items = connectors.list_connectors(conn)
        if args.as_json:
            _print_json(items)
        else:
            for item in items:
                print(
                    "{:<12} {:<12} {:<10} {}".format(
                        item["id"], item["status"], item["version"], item["name"]
                    )
                )
    elif args.connector_cmd == "connect":
        result = connectors.connect(conn, args.id, token_ref=args.token_ref)
        _print_json(result) if args.as_json else print("connected {}".format(result["id"]))
    elif args.connector_cmd == "auth":
        def on_url(url: str) -> None:
            print("Open this URL to authorize {}:\n{}".format(args.id, url))

        oauth.authorize(
            conn,
            home,
            args.id,
            open_browser=not args.no_browser,
            timeout=args.timeout,
            on_url=on_url,
        )
        print("authorized {}".format(args.id))
    elif args.connector_cmd == "disconnect":
        result = connectors.disconnect(conn, args.id, home=home)
        _print_json(result) if args.as_json else print("disconnected {}".format(result["id"]))
    elif args.connector_cmd == "sync":
        result = connectors.sync(conn, args.id, home=home)
        _print_json(result) if args.as_json else print(
            "synced {}: {} item(s), {} new".format(result["id"], result["items"], result["inserted"])
        )
    elif args.connector_cmd == "items":
        items = connectors.list_items(conn, args.id, kind=args.kind)
        if args.as_json:
            _print_json(items)
        else:
            for item in items:
                print("{}  {:<10} {}".format(item["external_id"], item["kind"], item["payload"]))


def _skill(args: argparse.Namespace, conn, home: Path) -> None:
    if args.skill_cmd == "list":
        items = skills.list_skills(conn)
        if args.as_json:
            _print_json(items)
        else:
            for item in items:
                print(
                    "{:<14} {:<8} {:<6} {}".format(
                        item["id"],
                        item["version"],
                        "builtin" if item.get("builtin") else "local",
                        ", ".join(item.get("permissions", [])),
                    )
                )
    elif args.skill_cmd == "show":
        _print_json(skills.get_skill(conn, args.id))
    elif args.skill_cmd == "install":
        manifest = json.loads(Path(args.path).expanduser().read_text(encoding="utf-8"))
        installed = skills.install(conn, manifest)
        _print_json(installed) if args.as_json else print(
            "installed {} v{}".format(installed["id"], installed["version"])
        )
    elif args.skill_cmd == "run":
        steps = skills.run(conn, args.id)
        run = runtime.propose_plan(
            conn,
            home,
            steps,
            intent="skill:{}".format(args.id),
            idempotency_key=args.idempotency_key,
        )
        _print_run(run, args.as_json, steps=True)
    elif args.skill_cmd == "uninstall":
        skills.uninstall(conn, args.id)
        print("uninstalled {}".format(args.id))


def _model(args: argparse.Namespace, conn) -> None:
    if args.model_cmd == "list":
        data = models.list_models(conn)
        if args.as_json:
            _print_json(data)
            return
        config = data["config"]
        print("provider:      {}".format(config["provider"]))
        print("model:         {}".format(config["model"]))
        print("fallback:      {}".format(config["fallback"] or "-"))
        print("base_url:      {}".format(config["base_url"]))
        print("api_key_ref:   {}".format(config["api_key_ref"]))
        print("daily_budget:  {}".format(config["daily_token_budget"] or "unlimited"))
        print("usage_today:   {} token(s)".format(data["usage_today"]))
        print("providers:     {}".format(", ".join(data["providers"])))
    elif args.model_cmd == "usage":
        rows = conn.execute(
            "SELECT * FROM model_usage ORDER BY ts DESC LIMIT ?", (args.limit,)
        ).fetchall()
        items = [dict(row) for row in rows]
        if args.as_json:
            _print_json(items)
        else:
            if not items:
                print("no model usage")
            for item in items:
                print(
                    "{}  {:<18} {:<12} task={:<10} tokens={}/{} cached={} ok={}".format(
                        item["ts"],
                        item["provider"],
                        item["model"],
                        item["task"],
                        item["prompt_tokens"],
                        item["completion_tokens"],
                        item["cached"],
                        item["ok"],
                    )
                )


def _mcp(args: argparse.Namespace, conn, home: Path) -> None:
    if args.mcp_cmd == "add":
        parts = shlex.split(args.command)
        if not parts:
            raise ValueError("command is empty")
        env = _parse_kv(args.env)
        server = mcp.add_server(conn, args.id, parts[0], parts[1:], env)
        _print_json(server) if args.as_json else print("registered MCP server {}".format(server["id"]))
    elif args.mcp_cmd == "list":
        servers = mcp.list_servers(conn)
        if args.as_json:
            _print_json(servers)
        else:
            if not servers:
                print("no MCP servers")
            for server in servers:
                print(
                    "{:<14} {:<12} {} {}".format(
                        server["id"],
                        server["status"],
                        server["command"],
                        " ".join(server["args"]),
                    )
                )
    elif args.mcp_cmd == "remove":
        mcp.remove_server(conn, args.id)
        print("removed MCP server {}".format(args.id))
    elif args.mcp_cmd == "tools":
        tools = mcp.server_tools(conn, args.id, cwd=home)
        if args.as_json:
            _print_json(tools)
        else:
            for tool in tools:
                print("{:<20} {}".format(tool.get("name", ""), tool.get("description", "")))
    elif args.mcp_cmd == "call":
        arguments = _parse_kv(args.arg)
        run = runtime.propose(
            conn,
            home,
            "mcp.call",
            {"server": args.id, "tool": args.tool, "arguments": arguments},
            intent=args.intent or "mcp {} {}".format(args.id, args.tool),
        )
        _print_run(run, args.as_json)


def _tick_result(result: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        _print_json(result)
        return
    print(
        "{}  {:<12} {:<10} already_ran={}".format(
            result.get("scheduled_for", ""),
            result.get("job", ""),
            result.get("status", ""),
            result.get("already_ran", False),
        )
    )
    if result.get("status") == "error":
        print("  error: {}".format(result.get("error")))


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


def _config(args: argparse.Namespace, conn) -> None:
    if args.config_cmd == "show":
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        settings = {row["key"]: row["value"] for row in rows}
        if args.as_json:
            _print_json(settings)
        else:
            if not settings:
                print("no settings")
            for key, value in settings.items():
                print("{:<32} {}".format(key, value))
    elif args.config_cmd == "set":
        set_setting(conn, args.key, args.value)
        print("{} = {}".format(args.key, args.value))


def _doctor(conn, home: Path, db_path: Path) -> None:
    version = migrations.current_version(conn)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    counts = {}
    for table in (
        "facts",
        "runs",
        "signals",
        "candidates",
        "connectors",
        "messages",
        "notifications",
        "model_usage",
        "mcp_servers",
    ):
        counts[table] = conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
    print("home:        {}".format(home))
    print("db:          {}".format(db_path))
    print("schema:      v{}".format(version))
    print("integrity:   {}".format(integrity))
    print("writable:    {}".format(os.access(home, os.W_OK)))
    print("api_token:   {}".format((home / "api_token").exists()))
    print("policy:      {}".format(json.dumps(policy.load_policy(conn), sort_keys=True)))
    print("counts:      {}".format(json.dumps(counts, sort_keys=True)))


def _bench(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        conn = connect(home / "db.sqlite")
        try:
            init_db(conn)
            results = bench.run(conn, home, facts=args.facts, runs=args.runs)
        finally:
            conn.close()
    if args.as_json:
        _print_json(results)
        return
    print("Daythread Bench v0")
    for key in sorted(results):
        print("  {:<36} {}".format(key, results[key]))


def _secret(args: argparse.Namespace, conn, home: Path) -> None:
    vault = SecretVault(conn, home)
    if args.secret_cmd == "list":
        names = vault.names()
        if args.as_json:
            _print_json(names)
        else:
            if not names:
                print("no secrets")
            for name in names:
                print(name)
    elif args.secret_cmd == "set":
        value = args.value
        if value is None:
            if sys.stdin.isatty():
                value = getpass.getpass("secret value: ")
            else:
                value = sys.stdin.read().strip()
        if not value:
            raise ValueError("secret value is empty")
        vault.set(args.name, value)
        print("stored secret {}".format(args.name))
    elif args.secret_cmd == "rm":
        vault.delete(args.name)
        print("removed secret {}".format(args.name))


def _passphrase(args: argparse.Namespace) -> str:
    if args.passphrase:
        return args.passphrase
    value = os.environ.get(args.passphrase_env)
    if value:
        return value
    if sys.stdin.isatty():
        return getpass.getpass("passphrase: ")
    raise ValueError(
        "passphrase required (set {} or pass --passphrase)".format(args.passphrase_env)
    )


def _sync(args: argparse.Namespace, conn, home: Path) -> None:
    passphrase = _passphrase(args)
    if args.sync_cmd == "export":
        path = sync.export_sync(
            conn,
            home,
            passphrase,
            Path(args.out).expanduser() if args.out else None,
        )
        _print_json({"path": str(path)}) if args.as_json else print(
            "sync bundle written to {}".format(path)
        )
    elif args.sync_cmd == "import":
        counts = sync.import_sync(
            conn, Path(args.path).expanduser(), passphrase, replace=args.replace
        )
        _print_json(counts) if args.as_json else print(
            "imported: {}".format(json.dumps(counts, sort_keys=True))
        )
    elif args.sync_cmd == "verify":
        info = sync.verify_sync(Path(args.path).expanduser(), passphrase)
        _print_json(info) if args.as_json else print(
            "sync OK: {} ({} fact(s), schema v{})".format(
                info["created_at"], info["counts"].get("facts", 0), info["schema_version"]
            )
        )


def _serve(args: argparse.Namespace, home: Path, db_path: Path) -> None:
    if args.print_token:
        print(ensure_token(home, rotate=args.rotate_token))
        return
    server = ApiServer(
        home,
        db_path,
        host=args.host,
        port=args.port,
        rotate_token=args.rotate_token,
    )
    print("daythread API on http://{}:{}".format(args.host, server.port))
    print("token: {}".format(server.token))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()


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

    connectors.connect(conn, "mock.gcal")
    connectors.connect(conn, "mock.gmail")
    connectors.sync(conn, "mock.gcal", now=now)
    connectors.sync(conn, "mock.gmail", now=now)
    proactivity.ingest_signal(
        conn,
        source="demo",
        kind="note",
        payload={"title": "Review the brand launch page"},
        dedupe_key="demo-brand-launch",
        now=now,
    )

    runtime.propose(
        conn,
        home,
        "calendar.add",
        {"title": "Design review", "start": (now + timedelta(hours=2)).isoformat()},
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
        {"to": "maya@example.com", "subject": "Deck", "body": "Latest version attached."},
        intent="demo draft",
    )
    external = runtime.propose(
        conn,
        home,
        "message.send",
        {"to": "maya@example.com", "subject": "Deck", "body": "Latest version attached."},
        intent="demo external send",
    )
    proactive = proactivity.proactive(conn, now=now, deliver=False)

    data = brief.morning_brief(conn, now=now)
    if args.as_json:
        _print_json(
            {
                "brief": data,
                "draft_run": draft["id"],
                "pending_run": external["id"],
                "proactive": proactive,
            }
        )
        return
    print(brief.render(data))
    print("")
    print("Draft run:    {} ({})".format(draft["id"][:8], draft["state"]))
    print("External run: {} ({})".format(external["id"][:8], external["state"]))
    print("Proactive:    {}/{} interrupts used, {} candidate(s)".format(
        proactive["budget"]["sent"], proactive["budget"]["max_sent"], len(proactive["candidates"])
    ))
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


def _print_run(
    run: Dict[str, Any],
    as_json: bool,
    steps: bool = False,
    verbose: bool = False,
) -> None:
    if as_json:
        _print_json(run)
        return
    print(
        "run {}  [{}]  tool={}  risk={}  decision={}".format(
            run["id"][:8], run["state"], run["tool"], run["risk"], run["decision"]
        )
    )
    print("  intent: {}".format(run["intent"]))
    plan_steps = (run.get("plan") or {}).get("steps", [])
    if len(plan_steps) > 1:
        print("  plan:")
        for step in plan_steps:
            deps = ",".join(step.get("depends_on", [])) or "-"
            print("    {:<4} {:<18} deps={}".format(step["id"], step["tool"], deps))
    if run.get("budget"):
        print("  budget: {}".format(json.dumps(run["budget"], sort_keys=True)))
    if run.get("checkpoint"):
        done = run["checkpoint"].get("completed", [])
        if done:
            print("  checkpoint: {}".format(", ".join(done)))
    if run["state"] == "pending_approval":
        print("  next:   dt approve {}".format(run["id"]))
    elif run["state"] == "failed":
        print("  next:   dt resume {}  |  dt show {}".format(run["id"], run["id"]))
    elif run["state"] == "completed":
        if _is_undoable(run):
            print("  next:   dt undo {}  |  dt ledger {} --verify".format(run["id"], run["id"]))
        else:
            print("  next:   dt ledger {} --verify".format(run["id"]))
    if steps:
        for step in run.get("steps", []):
            if verbose:
                detail = json.dumps(step["payload"], ensure_ascii=False)
            else:
                payload = step.get("payload") or {}
                detail = payload.get("summary", "") if isinstance(payload, dict) else ""
            print("  [{:>2}] {:<18} {}".format(step["seq"], step["kind"], detail))


def _is_undoable(run: Dict[str, Any]) -> bool:
    for step in run.get("steps", []):
        if step.get("kind") not in ("executed", "step.succeeded"):
            continue
        payload = step.get("payload") or {}
        if payload.get("idempotent_hit"):
            continue
        if payload.get("irreversible"):
            return False
        if payload.get("undo"):
            return True
    return False


def _print_facts(facts: List[Dict[str, Any]]) -> None:
    if not facts:
        print("no facts")
        return
    for fact in facts:
        deleted = " (deleted)" if fact.get("deleted_at") else ""
        tags = " tags={}".format(fact.get("tags")) if fact.get("tags") else ""
        print(
            "{}  {:<9} {} · {} · {}{}{}".format(
                fact["id"][:8],
                fact["subject_type"],
                fact["subject_id"],
                fact["predicate"],
                fact["object"],
                tags,
                deleted,
            )
        )


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
