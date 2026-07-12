"""AMQP keepalive: pump events while worker runs long jobs."""

from __future__ import annotations

from typing import Any

_conn: Any = None


def set_connection(connection: Any) -> None:
    global _conn
    _conn = connection


def clear_connection() -> None:
    global _conn
    _conn = None


def pump_events() -> None:
    """Let pika answer RabbitMQ heartbeats during blocking pipeline work."""
    if _conn is None:
        return
    try:
        if getattr(_conn, "is_closed", False):
            return
        _conn.process_data_events(time_limit=0)
    except Exception:
        pass
