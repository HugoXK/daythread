"""Exportable compliance evidence for local deployments."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from . import __version__, maintenance, migrations, policy
from .db import utc_now

SENSITIVE = ("token", "secret", "password", "api_key", "cookie")


def evidence(conn: sqlite3.Connection, home: Path) -> Dict[str, Any]:
    settings = {}
    for row in conn.execute("SELECT key, value FROM settings ORDER BY key"):
        if any(marker in row["key"].lower() for marker in SENSITIVE):
            continue
        settings[row["key"]] = row["value"]
    return {
        "generated_at": utc_now(),
        "version": __version__,
        "schema_version": migrations.current_version(conn),
        "latest_schema": migrations.LATEST_VERSION,
        "home": str(home),
        "policy": policy.load_policy(conn),
        "settings": settings,
        "stats": maintenance.stats(conn),
        "controls": _controls(),
        "gaps": _gaps(),
    }


def write_evidence(conn: sqlite3.Connection, home: Path, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(evidence(conn, home), ensure_ascii=False, indent=2, sort_keys=True)
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return out


def _controls() -> List[Dict[str, Any]]:
    return [
        {
            "id": "access-control",
            "name": "Logical access",
            "status": "implemented",
            "evidence": [
                "loopback API",
                "bearer token 0600",
                "Host allowlist",
                "OAuth scopes + revoke",
                "relay device tokens",
            ],
        },
        {
            "id": "change-management",
            "name": "Change management",
            "status": "implemented",
            "evidence": [
                "forward-only migrations",
                "CI lint + tests + coverage gate",
                "contract and migration-matrix tests",
                "release manifest + SBOM + attestation",
            ],
        },
        {
            "id": "data-protection",
            "name": "Data protection",
            "status": "implemented",
            "evidence": [
                "AES-GCM secrets",
                "encrypted model cache",
                "signed/encrypted backups",
                "E2EE sync",
                "sensitivity filters",
            ],
        },
        {
            "id": "monitoring",
            "name": "Monitoring and audit",
            "status": "implemented",
            "evidence": [
                "hash-chained run ledger",
                "audit log",
                "Prometheus /metrics",
                "Grafana starter dashboard",
            ],
        },
        {
            "id": "resilience",
            "name": "Resilience",
            "status": "implemented",
            "evidence": [
                "atomic run leases",
                "checkpoints/resume/cancel",
                "retention/prune",
                "ETag conflict handling",
            ],
        },
        {
            "id": "privacy",
            "name": "Privacy rights",
            "status": "implemented",
            "evidence": [
                "dt privacy export/forget/purge",
                "retention settings",
                "local-first by default",
            ],
        },
    ]


def _gaps() -> List[str]:
    return [
        "No SOC 2 / ISO 27001 certificate.",
        "No HIPAA/BAA; health features are lifestyle support only.",
        "Single-user local model; no hosted multi-tenant isolation.",
        "Provider-side deletion and model-provider retention are out of scope.",
        "Relay is self-hosted; operators own TLS, ACLs, and retention.",
    ]
