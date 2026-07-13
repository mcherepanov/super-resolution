"""Сводка для мобильного клиента (Android): очередь и worker."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pika

from db import get_conn
from messaging import QUEUE_NAME, rabbit_connection_params


def _rabbit_queue_stats() -> tuple[int, int]:
    """(сообщений в очереди ready, число consumer)."""
    try:
        conn = pika.BlockingConnection(rabbit_connection_params(heartbeat=None))
        try:
            ch = conn.channel()
            q = ch.queue_declare(queue=QUEUE_NAME, passive=True)
            return int(q.method.message_count), int(q.method.consumer_count)
        finally:
            conn.close()
    except (pika.exceptions.AMQPConnectionError, pika.exceptions.ChannelClosedByBroker):
        return 0, 0


def _jobs_queued() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued'",
        ).fetchone()
        return int(row["n"]) if row else 0


def _jobs_processing() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = 'processing'",
        ).fetchone()
        return int(row["n"]) if row else 0


def _tasks_completed_today() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM jobs
            WHERE status = 'done'
              AND finished_at IS NOT NULL
              AND substr(finished_at, 1, 10) = ?
            """,
            (today,),
        ).fetchone()
        return int(row["n"]) if row else 0


def build_mobile_status() -> dict:
    """
    Плоский JSON для GET /api/mobile-status.
    queue_size — max(queued в SQLite, ready в RabbitMQ).
    workers_total — consumer_count из RabbitMQ (0 если worker не подключён).
    workers_busy — jobs в processing (0 или 1 при одном GPU).
    """
    rmq_ready, consumers = _rabbit_queue_stats()
    queued = _jobs_queued()
    processing = _jobs_processing()
    queue_size = max(queued, rmq_ready)

    return {
        "status": "ok",
        "timestamp": int(time.time()),
        "queue_size": queue_size,
        "workers_total": consumers,
        "workers_busy": processing,
        "tasks_completed_today": _tasks_completed_today(),
    }
