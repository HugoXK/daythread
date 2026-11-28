"""Daythread Bench v0: memory, proactivity, runtime, and safety smoke."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict

from . import memory, proactivity, receipt, runtime


def _receipt_tamper_case(conn, home: Path) -> bool:
    run = runtime.propose(
        conn,
        home,
        "note.write",
        {"path": "bench/receipt-case.md", "content": "receipt"},
        intent="bench receipt safety",
    )
    if run["state"] != "completed":
        return False
    data = receipt.build_receipt(conn, run["id"])
    if not receipt.verify_receipt(data).get("ok"):
        return False
    tampered = json.loads(receipt.to_json(data))
    tampered["steps"][-1]["payload_digest"] = "0" * 64
    return receipt.verify_receipt(tampered).get("ok") is False


def _undo_restore_case(conn, home: Path) -> bool:
    path = home / "notes" / "bench" / "undo-case.md"

    def snapshot():
        if path.exists():
            return (True, path.read_text(encoding="utf-8"))
        return (False, None)

    baseline = snapshot()
    run = runtime.propose(
        conn,
        home,
        "note.write",
        {"path": "bench/undo-case.md", "content": "bench-undo"},
        intent="bench undo safety",
    )
    if run["state"] != "completed":
        return False
    undone = runtime.undo_run(conn, home, run["id"])
    first = snapshot()
    second = snapshot()
    return undone["state"] == "undone" and first == second == baseline


def run(
    conn,
    home: Path,
    facts: int = 300,
    runs: int = 20,
) -> Dict[str, Any]:
    facts = max(int(facts), 0)
    runs = max(int(runs), 0)
    results: Dict[str, Any] = {"facts": facts, "runs": runs}

    started = time.perf_counter()
    for index in range(facts):
        memory.add_fact(
            conn,
            "person",
            "person-{}".format(index),
            "role",
            "engineer",
            source="bench",
        )
    elapsed = max(time.perf_counter() - started, 1e-9)
    results["memory_insert_per_sec"] = round(facts / elapsed, 1)
    results["memory_insert_ms"] = round(elapsed * 1000.0, 1)

    latencies = []
    for _ in range(10):
        for query in ("person-1", "engineer", "role"):
            started = time.perf_counter()
            memory.search_facts(conn, query)
            latencies.append(time.perf_counter() - started)
    results["memory_search_ms_mean"] = round(statistics.mean(latencies) * 1000.0, 3)
    results["memory_search_ms_max"] = round(max(latencies) * 1000.0, 3)

    started = time.perf_counter()
    proactive = proactivity.proactive(conn, deliver=False)
    results["proactivity_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    results["proactivity_candidates"] = len(proactive["candidates"])

    started = time.perf_counter()
    for index in range(runs):
        runtime.propose_plan(
            conn,
            home,
            [
                {
                    "id": "s1",
                    "tool": "note.write",
                    "args": {"path": "bench/{}.md".format(index), "content": "x"},
                }
            ],
            intent="bench",
        )
    elapsed = max(time.perf_counter() - started, 1e-9)
    results["runtime_plan_ms"] = round((elapsed / max(runs, 1)) * 1000.0, 2)
    results["runtime_runs_per_sec"] = round(runs / elapsed, 1) if runs else 0.0

    escape = runtime.propose(
        conn,
        home,
        "note.write",
        {"path": "../escape.md", "content": "x"},
        intent="bench safety",
    )
    external = runtime.propose(
        conn,
        home,
        "message.send",
        {"to": "bench@example.com", "subject": "bench", "body": "x"},
        intent="bench safety",
    )
    results["safety_path_escape_blocked"] = escape["state"] == "failed"
    results["safety_external_requires_approval"] = external["state"] == "pending_approval"

    started = time.perf_counter()
    receipt_ok = _receipt_tamper_case(conn, home)
    receipt_ms = round((time.perf_counter() - started) * 1000.0, 1)
    started = time.perf_counter()
    undo_ok = _undo_restore_case(conn, home)
    undo_ms = round((time.perf_counter() - started) * 1000.0, 1)
    results["safety_receipt_tamper_rejected"] = bool(receipt_ok)
    results["safety_undo_restores_baseline"] = bool(undo_ok)
    results["safety_cases"] = [
        {"name": "receipt_tamper_rejected", "pass": bool(receipt_ok), "ms": receipt_ms},
        {"name": "undo_restores_baseline", "pass": bool(undo_ok), "ms": undo_ms},
    ]
    results["safety_pass"] = (
        results["safety_path_escape_blocked"]
        and results["safety_external_requires_approval"]
        and receipt_ok
        and undo_ok
    )
    return results
