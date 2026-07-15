"""Статистика задач с учётом таймзоны клиента."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db import get_conn, list_jobs
from download_utils import count_ready_downloads

INPUT_DIR = Path(os.environ.get("INPUT_DIR", "/app/input"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/output"))


def resolve_timezone(tz_name: str | None) -> ZoneInfo:
    name = (tz_name or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def _count_status_in_local_day(
    *,
    status: str,
    day: date,
    tz: ZoneInfo,
) -> int:
    start, end = _local_day_bounds(day, tz)
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT finished_at FROM jobs
            WHERE status = ?
              AND finished_at IS NOT NULL
            """,
            (status,),
        ).fetchall()
    n = 0
    for row in rows:
        finished = _parse_iso(row["finished_at"] if isinstance(row, dict) else row[0])
        if finished is None:
            continue
        if start_utc <= finished.astimezone(timezone.utc) < end_utc:
            n += 1
    return n


def build_stats(tz_name: str | None = None) -> dict[str, Any]:
    tz = resolve_timezone(tz_name)
    now_local = datetime.now(tz)
    today = now_local.date()
    yesterday = today - timedelta(days=1)

    done_today = _count_status_in_local_day(status="done", day=today, tz=tz)
    done_yesterday = _count_status_in_local_day(status="done", day=yesterday, tz=tz)
    failed_today = _count_status_in_local_day(status="failed", day=today, tz=tz)
    cancelled_today = _count_status_in_local_day(status="cancelled", day=today, tz=tz)

    with get_conn() as conn:
        queued = int(
            conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued'").fetchone()["n"]
        )
        processing = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status = 'processing'"
            ).fetchone()["n"]
        )

    ready = count_ready_downloads(
        list_jobs(),
        input_dir=INPUT_DIR,
        output_dir=OUTPUT_DIR,
    )

    return {
        "status": "ok",
        "timezone": str(tz),
        "local_date": today.isoformat(),
        "local_yesterday": yesterday.isoformat(),
        "completed_today": done_today,
        "completed_yesterday": done_yesterday,
        "failed_today": failed_today,
        "cancelled_today": cancelled_today,
        "ready_downloads": ready,
        "queue_size": queued,
        "workers_busy": processing,
    }
