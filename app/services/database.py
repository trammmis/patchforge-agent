from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.config import settings
from app.storage.models import SQLITE_SCHEMA


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    settings.ensure_dirs()
    path = Path(db_path) if db_path else settings.data_dir / "patchforge.sqlite3"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SQLITE_SCHEMA)
    _migrate_tasks_table(conn)
    return conn


def _migrate_tasks_table(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    columns = {
        "repo_path": "TEXT",
        "task_payload": "TEXT",
        "result_payload": "TEXT",
        "failure_type": "TEXT",
        "error_message": "TEXT",
        "report_path": "TEXT",
        "trace_path": "TEXT",
        "queued_at": "REAL",
        "started_at": "REAL",
        "finished_at": "REAL",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")
    conn.commit()
