"""Forward-only schema migrations."""

from __future__ import annotations

import sqlite3

LATEST_VERSION = 3


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def migrate(conn: sqlite3.Connection) -> int:
    version = current_version(conn)
    if version < 1:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '1')")
        conn.commit()
        version = 1
    if version < 2:
        _migrate_v2(conn)
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '2')")
        conn.commit()
        version = 2
    if version < 3:
        _migrate_v3(conn)
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '3')")
        conn.commit()
        version = 3
    return version


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row["name"] for row in conn.execute("PRAGMA table_info({})".format(table))}


def _add_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    if name not in _columns(conn, table):
        conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, name, ddl))


def _migrate_v2(conn: sqlite3.Connection) -> None:
    _add_column(conn, "facts", "sensitivity", "TEXT NOT NULL DEFAULT 'normal'")
    _add_column(conn, "facts", "tags", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "facts", "metadata", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "facts", "score", "REAL NOT NULL DEFAULT 1.0")
    _add_column(conn, "facts", "last_accessed_at", "TEXT")
    _add_column(conn, "facts", "supersedes", "TEXT")

    _add_column(conn, "runs", "plan", "TEXT")
    _add_column(conn, "runs", "budget", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "runs", "checkpoint", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "runs", "trace_id", "TEXT")
    _add_column(conn, "runs", "idempotency_key", "TEXT")
    _add_column(conn, "runs", "error", "TEXT")
    _add_column(conn, "runs", "parent_run", "TEXT")

    _add_column(conn, "run_steps", "step_key", "TEXT")
    _add_column(conn, "run_steps", "depends_on", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "run_steps", "attempt", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "run_steps", "status", "TEXT NOT NULL DEFAULT 'ok'")

    _add_column(conn, "calendar_events", "external_id", "TEXT")
    _add_column(conn, "calendar_events", "connector", "TEXT")
    _add_column(conn, "tasks", "external_id", "TEXT")
    _add_column(conn, "tasks", "connector", "TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fact_history (
            id       TEXT PRIMARY KEY,
            fact_id  TEXT NOT NULL,
            event_id TEXT,
            action   TEXT NOT NULL,
            before   TEXT,
            after    TEXT,
            ts       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fact_history_fact
            ON fact_history(fact_id, ts);

        CREATE TABLE IF NOT EXISTS signals (
            id         TEXT PRIMARY KEY,
            source     TEXT NOT NULL,
            kind       TEXT NOT NULL,
            payload    TEXT NOT NULL,
            ts         TEXT NOT NULL,
            taint      TEXT NOT NULL DEFAULT 'trusted',
            dedupe_key TEXT,
            processed  INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_signals_dedupe
            ON signals(dedupe_key, ts);

        CREATE TABLE IF NOT EXISTS candidates (
            id            TEXT PRIMARY KEY,
            signal_id     TEXT,
            kind          TEXT NOT NULL,
            title         TEXT NOT NULL,
            body          TEXT,
            score         REAL NOT NULL,
            urgency       REAL NOT NULL,
            actionability REAL NOT NULL,
            novelty       REAL NOT NULL,
            status        TEXT NOT NULL DEFAULT 'new',
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_candidates_status
            ON candidates(status, score);

        CREATE TABLE IF NOT EXISTS briefings (
            id           TEXT PRIMARY KEY,
            kind         TEXT NOT NULL,
            date         TEXT NOT NULL,
            payload      TEXT NOT NULL,
            delivered_at TEXT,
            created_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id     TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            action TEXT NOT NULL,
            ts     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS budgets (
            date     TEXT PRIMARY KEY,
            sent     INTEGER NOT NULL DEFAULT 0,
            max_sent INTEGER NOT NULL DEFAULT 3
        );

        CREATE TABLE IF NOT EXISTS connectors (
            id           TEXT PRIMARY KEY,
            version      TEXT NOT NULL,
            status       TEXT NOT NULL,
            scopes       TEXT NOT NULL DEFAULT '[]',
            connected_at TEXT,
            last_sync    TEXT,
            token_ref    TEXT,
            error        TEXT
        );

        CREATE TABLE IF NOT EXISTS connector_items (
            id          TEXT PRIMARY KEY,
            connector   TEXT NOT NULL,
            kind        TEXT NOT NULL,
            external_id TEXT NOT NULL,
            payload     TEXT NOT NULL,
            ts          TEXT NOT NULL,
            UNIQUE(connector, kind, external_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          TEXT PRIMARY KEY,
            external_id TEXT,
            thread_id   TEXT,
            sender      TEXT,
            subject     TEXT,
            snippet     TEXT,
            body        TEXT,
            ts          TEXT,
            taint       TEXT NOT NULL DEFAULT 'untrusted'
        );

        CREATE TABLE IF NOT EXISTS skills (
            id           TEXT PRIMARY KEY,
            version      TEXT NOT NULL,
            manifest     TEXT NOT NULL,
            installed_at TEXT NOT NULL,
            enabled      INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS audit (
            id       TEXT PRIMARY KEY,
            ts       TEXT NOT NULL,
            actor    TEXT NOT NULL,
            action   TEXT NOT NULL,
            target   TEXT,
            detail   TEXT,
            trace_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);

        CREATE TABLE IF NOT EXISTS metrics (
            name       TEXT PRIMARY KEY,
            value      REAL NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS idempotency (
            key      TEXT PRIMARY KEY,
            run_id   TEXT NOT NULL,
            step_key TEXT NOT NULL,
            status   TEXT NOT NULL,
            result   TEXT,
            ts       TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_idempotency
            ON runs(idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        """
    )


def _migrate_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_usage (
            id                TEXT PRIMARY KEY,
            ts                TEXT NOT NULL,
            provider          TEXT NOT NULL,
            model             TEXT NOT NULL,
            task              TEXT NOT NULL,
            prompt_tokens     INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms        REAL NOT NULL DEFAULT 0,
            ok                INTEGER NOT NULL DEFAULT 1,
            cached            INTEGER NOT NULL DEFAULT 0,
            error             TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_model_usage_ts ON model_usage(ts);

        CREATE TABLE IF NOT EXISTS model_cache (
            key        TEXT PRIMARY KEY,
            provider   TEXT NOT NULL,
            model      TEXT NOT NULL,
            response   TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mcp_servers (
            id         TEXT PRIMARY KEY,
            command    TEXT NOT NULL,
            args       TEXT NOT NULL DEFAULT '[]',
            env        TEXT NOT NULL DEFAULT '{}',
            status     TEXT NOT NULL DEFAULT 'configured',
            created_at TEXT NOT NULL,
            last_error TEXT
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id           TEXT PRIMARY KEY,
            ts           TEXT NOT NULL,
            kind         TEXT NOT NULL,
            title        TEXT NOT NULL,
            body         TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'queued',
            delivered_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status, ts);

        CREATE TABLE IF NOT EXISTS scheduler_runs (
            id            TEXT PRIMARY KEY,
            job           TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            started_at    TEXT NOT NULL,
            finished_at   TEXT,
            status        TEXT NOT NULL,
            detail        TEXT NOT NULL DEFAULT '{}',
            UNIQUE(job, scheduled_for)
        );

        CREATE INDEX IF NOT EXISTS idx_facts_active
            ON facts(deleted_at, superseded_by, updated_at);
        CREATE INDEX IF NOT EXISTS idx_runs_state
            ON runs(state, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
        CREATE INDEX IF NOT EXISTS idx_candidates_score
            ON candidates(status, score);
        CREATE INDEX IF NOT EXISTS idx_signals_processed
            ON signals(processed, ts);
        """
    )
