"""Risk tiers and the allow / ask / deny policy gate."""

from __future__ import annotations

import sqlite3
from typing import Dict

RISKS = ("read", "write", "external", "destructive")
DECISIONS = ("allow", "ask", "deny")

DEFAULTS: Dict[str, str] = {
    "read": "allow",
    "write": "allow",
    "external": "ask",
    "destructive": "ask",
}


def load_policy(conn: sqlite3.Connection) -> Dict[str, str]:
    policy = dict(DEFAULTS)
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'policy.%'"
    ).fetchall()
    for row in rows:
        risk = row["key"].split(".", 1)[1]
        if risk in policy and row["value"] in DECISIONS:
            policy[risk] = row["value"]
    return policy


def set_policy(conn: sqlite3.Connection, risk: str, decision: str) -> None:
    if risk not in RISKS:
        raise ValueError("unknown risk tier: {} (expected one of {})".format(risk, ", ".join(RISKS)))
    if decision not in DECISIONS:
        raise ValueError(
            "unknown decision: {} (expected one of {})".format(decision, ", ".join(DECISIONS))
        )
    conn.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
        ("policy.{}".format(risk), decision),
    )
    conn.commit()


def decide(conn: sqlite3.Connection, risk: str) -> str:
    if risk not in RISKS:
        raise ValueError("unknown risk tier: {}".format(risk))
    return load_policy(conn)[risk]
