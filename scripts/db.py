"""SQLite: журнал задач обработки."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("DATABASE_PATH", "/app/data/app.db"))

STATUSES = frozenset({"queued", "processing", "done", "failed", "skipped"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "options" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN options TEXT")
    if "output_format" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN output_format TEXT DEFAULT 'wav'")
    if "job_type" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN job_type TEXT DEFAULT 'process'")


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                input_path TEXT NOT NULL,
                output_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error_message TEXT,
                duration_sec REAL,
                input_sr INTEGER,
                output_sr INTEGER,
                options TEXT,
                output_format TEXT DEFAULT 'wav',
                job_type TEXT DEFAULT 'process'
            )
            """
        )
        _migrate(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)"
        )
        conn.commit()


def create_job(
    filename: str,
    input_path: str,
    output_path: str,
    *,
    options: dict[str, Any] | None = None,
    output_format: str = "wav",
    job_type: str = "process",
) -> int:
    opts_json = json.dumps(options or {}, ensure_ascii=False)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (
                filename, input_path, output_path, status, created_at,
                options, output_format, job_type
            )
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (filename, input_path, output_path, _now(), opts_json, output_format, job_type),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_job(job_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"invalid status: {fields['status']}")
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", vals)
        conn.commit()


def list_jobs(limit: int = 200) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def has_active_job(input_path: str, output_path: str) -> bool:
    """Есть ли queued/processing для этой пары вход → выход."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM jobs
            WHERE input_path = ? AND output_path = ?
              AND status IN ('queued', 'processing')
            LIMIT 1
            """,
            (input_path, output_path),
        ).fetchone()
        return row is not None


def delete_job(job_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
