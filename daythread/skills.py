"""Declarative skills: validated plans with explicit permissions."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from . import obs, planner
from .db import canonical, json_load, row_to_dict, utc_now
from .tools import get_tool


def _manifest(skill_id: str, name: str, description: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = planner.validate(steps)
    permissions = sorted({get_tool(step["tool"]).capability for step in normalized})
    return {
        "id": skill_id,
        "name": name,
        "version": "1.0.0",
        "description": description,
        "steps": normalized,
        "permissions": permissions,
        "builtin": True,
    }


BUILTIN: Dict[str, Dict[str, Any]] = {
    "morning.brief": _manifest(
        "morning.brief",
        "Morning brief",
        "Generate the morning brief from local state.",
        [{"id": "s1", "tool": "brief.generate", "args": {}}],
    ),
    "evening.review": _manifest(
        "evening.review",
        "Evening review",
        "Generate the evening brief from local state.",
        [{"id": "s1", "tool": "brief.generate", "args": {"evening": True}}],
    ),
    "inbox.triage": _manifest(
        "inbox.triage",
        "Inbox triage",
        "Sync the mock inbox, then produce a brief.",
        [
            {"id": "s1", "tool": "connector.sync", "args": {"connector": "mock.gmail"}},
            {"id": "s2", "tool": "brief.generate", "args": {}, "depends_on": ["s1"]},
        ],
    ),
    "weekly.review": _manifest(
        "weekly.review",
        "Weekly review",
        "Collect calendar and tasks, then produce a brief.",
        [
            {"id": "s1", "tool": "calendar.list", "args": {}},
            {"id": "s2", "tool": "task.list", "args": {}},
            {"id": "s3", "tool": "brief.generate", "args": {}, "depends_on": ["s1", "s2"]},
        ],
    ),
}


def list_skills(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    installed = {}
    for row in conn.execute("SELECT * FROM skills").fetchall():
        item = row_to_dict(row)
        manifest = json_load(item["manifest"])
        manifest["builtin"] = manifest.get("id") in BUILTIN
        manifest["enabled"] = bool(item["enabled"])
        installed[manifest["id"]] = manifest
    result = []
    for skill_id in sorted(set(BUILTIN) | set(installed)):
        result.append(installed.get(skill_id) or BUILTIN[skill_id])
    return result


def get_skill(conn: sqlite3.Connection, skill_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    if row:
        manifest = json_load(row["manifest"])
        manifest["enabled"] = bool(row["enabled"])
        manifest["builtin"] = skill_id in BUILTIN
        return manifest
    if skill_id in BUILTIN:
        return dict(BUILTIN[skill_id])
    raise KeyError(
        "unknown skill: {} (available: {})".format(
            skill_id, ", ".join(sorted(set(BUILTIN)))
        )
    )


def install(conn: sqlite3.Connection, manifest: Dict[str, Any]) -> Dict[str, Any]:
    required = ("id", "version", "steps")
    for key in required:
        if key not in manifest:
            raise ValueError("skill manifest missing {}".format(key))
    steps = planner.validate(manifest["steps"])
    permissions = sorted({get_tool(step["tool"]).capability for step in steps})
    stored = {
        "id": str(manifest["id"]),
        "name": str(manifest.get("name", manifest["id"])),
        "version": str(manifest["version"]),
        "description": str(manifest.get("description", "")),
        "steps": steps,
        "permissions": permissions,
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO skills(id, version, manifest, installed_at, enabled)
        VALUES(?, ?, ?, ?, 1)
        """,
        (stored["id"], stored["version"], canonical(stored), utc_now()),
    )
    conn.commit()
    obs.audit(conn, "skill.installed", actor="user", target=stored["id"], detail={"version": stored["version"]})
    return stored


def uninstall(conn: sqlite3.Connection, skill_id: str) -> None:
    if skill_id in BUILTIN:
        raise ValueError("cannot uninstall a builtin skill")
    conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
    conn.commit()
    obs.audit(conn, "skill.uninstalled", actor="user", target=skill_id)


def run(conn: sqlite3.Connection, skill_id: str) -> List[Dict[str, Any]]:
    manifest = get_skill(conn, skill_id)
    if manifest.get("enabled") is False:
        raise ValueError("skill is disabled: {}".format(skill_id))
    return planner.validate(manifest["steps"])
