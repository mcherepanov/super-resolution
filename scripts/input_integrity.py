"""Целостность файлов в input/: проверка после загрузки."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db import get_conn, utc_now_iso
from ffmpeg_ops import AudioIntegrityError, validate_input_audio


def init_input_integrity() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS input_integrity (
                filename TEXT PRIMARY KEY,
                corrupted INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                checked_at TEXT NOT NULL,
                size_bytes INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def delete_input_integrity(filename: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM input_integrity WHERE filename = ?",
            (Path(filename).name,),
        )
        conn.commit()


def _get_row(filename: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM input_integrity WHERE filename = ?",
            (Path(filename).name,),
        ).fetchone()
        return dict(row) if row else None


def _save_row(filename: str, *, corrupted: bool, error_message: str | None, size_bytes: int) -> None:
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO input_integrity (filename, corrupted, error_message, checked_at, size_bytes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(filename) DO UPDATE SET
                corrupted = excluded.corrupted,
                error_message = excluded.error_message,
                checked_at = excluded.checked_at,
                size_bytes = excluded.size_bytes
            """,
            (
                Path(filename).name,
                1 if corrupted else 0,
                error_message,
                now,
                size_bytes,
            ),
        )
        conn.commit()


def record_input_check(path: Path) -> bool:
    """Проверить файл и сохранить результат. True если файл целый."""
    path = path.resolve()
    size = path.stat().st_size
    try:
        validate_input_audio(path)
    except AudioIntegrityError as exc:
        _save_row(path.name, corrupted=True, error_message=str(exc)[:2000], size_bytes=size)
        return False
    _save_row(path.name, corrupted=False, error_message=None, size_bytes=size)
    return True


def ensure_input_integrity(path: Path) -> dict[str, Any]:
    """Актуальный статус файла (перепроверка при смене размера)."""
    path = path.resolve()
    if not path.is_file():
        return {"corrupted": False, "error_message": None}
    size = path.stat().st_size
    row = _get_row(path.name)
    if row is not None and row.get("size_bytes") == size:
        return {
            "corrupted": bool(row.get("corrupted")),
            "error_message": row.get("error_message"),
        }
    ok = record_input_check(path)
    row = _get_row(path.name)
    if row is None:
        return {"corrupted": not ok, "error_message": None}
    return {
        "corrupted": bool(row.get("corrupted")),
        "error_message": row.get("error_message"),
    }


def is_input_corrupted(path: Path) -> bool:
    return ensure_input_integrity(path)["corrupted"]
