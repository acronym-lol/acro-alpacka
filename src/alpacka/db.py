"""SQLite registry for projects + templates.

Schema is created lazily on first open. The `user_version` pragma drives
migrations; see `_MIGRATIONS` for the dispatch chain.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import paths

SCHEMA_VERSION = 8


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    added_at TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    last_launched TEXT,
    stale INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'touchdesigner',
    approved_run_hash TEXT
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    toe_path TEXT NOT NULL,
    builtin INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'touchdesigner'
);

CREATE TABLE IF NOT EXISTS running_processes (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pid INTEGER NOT NULL UNIQUE,
    toe_file TEXT NOT NULL DEFAULT '',
    td_version TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'touchdesigner',
    process_name_pattern TEXT NOT NULL DEFAULT 'touchdesigner',
    run_config_name TEXT NOT NULL DEFAULT 'default',
    output_mode TEXT NOT NULL DEFAULT 'none',
    exit_code INTEGER,
    completed_at TEXT,
    keep_alive_after_main INTEGER NOT NULL DEFAULT 0,
    oneshot INTEGER NOT NULL DEFAULT 0,
    create_time REAL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_file: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    _add_column(conn, "projects", "kind", "TEXT NOT NULL DEFAULT 'touchdesigner'")
    _add_column(conn, "templates", "kind", "TEXT NOT NULL DEFAULT 'touchdesigner'")
    _add_column(conn, "running_processes", "kind", "TEXT NOT NULL DEFAULT 'touchdesigner'")
    _add_column(
        conn, "running_processes", "process_name_pattern", "TEXT NOT NULL DEFAULT 'touchdesigner'"
    )
    _add_column(conn, "running_processes", "run_config_name", "TEXT NOT NULL DEFAULT 'default'")
    _add_column(conn, "running_processes", "exit_code", "INTEGER")
    _add_column(conn, "running_processes", "completed_at", "TEXT")


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """v4 — track output_mode per running process so terminate/cleanup logic
    can branch on inline-vs-console without re-deriving from the project."""
    _add_column(conn, "running_processes", "output_mode", "TEXT NOT NULL DEFAULT 'none'")


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """v5 — launch dependencies. Two flags per row let the crash monitor
    decide whether to kill a dep when the main process dies, and let
    sweep_dead distinguish one-shot deps that should persist for UI
    display from long-running deps."""
    _add_column(
        conn, "running_processes", "keep_alive_after_main", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column(conn, "running_processes", "oneshot", "INTEGER NOT NULL DEFAULT 0")


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """v6 — the built-in TD template was renamed `td-default-project` →
    `acro-td-core`. Drop the now-orphaned old built-in row so it doesn't
    linger as a dead, source-missing entry; `ensure_builtins()` seeds the
    new `acro-td-core` row on startup. The name is a literal (not the
    renamed constant) because it targets pre-existing rows."""
    conn.execute(
        "DELETE FROM templates WHERE name = 'td-default-project' AND builtin = 1"
    )


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """v7 — per-machine approval of each project's run settings (see
    `approval.py`). Projects already in the library are marked
    'pending-upgrade' so `approval.grandfather_existing()` approves them
    as they are at the next startup instead of asking about every one."""
    _add_column(conn, "projects", "approved_run_hash", "TEXT")
    conn.execute("UPDATE projects SET approved_run_hash = 'pending-upgrade'")


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """v8 — store each tracked process's OS start time so a PID that the
    OS has reused for another program is never focused or killed. Older
    rows keep NULL and fall back to the process-name check."""
    _add_column(conn, "running_processes", "create_time", "REAL")


def init() -> None:
    """Create the DB file and schema if missing; migrate older schemas forward."""
    paths.ensure_dirs()
    # Use autocommit mode (isolation_level=None) so schema changes and the
    # user_version bump persist immediately — pragma writes don't play well
    # with Python's default deferred-transaction mode.
    conn = sqlite3.connect(paths.db_path(), isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current >= SCHEMA_VERSION:
            return
        if current == 0:
            conn.executescript(SCHEMA_SQL)
        else:
            if current <= 2:
                _migrate_v2_to_v3(conn)
            if current <= 3:
                _migrate_v3_to_v4(conn)
            if current <= 4:
                _migrate_v4_to_v5(conn)
            if current <= 5:
                _migrate_v5_to_v6(conn)
            if current <= 6:
                _migrate_v6_to_v7(conn)
            if current <= 7:
                _migrate_v7_to_v8(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        conn.close()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Context-managed DB connection. Commits on successful exit."""
    init()
    conn = _connect(paths.db_path())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
