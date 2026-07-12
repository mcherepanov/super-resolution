"""Запрос и проверка прерывания job (кооперативная отмена)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from db import get_job, update_job, utc_now_iso

_current_job_id: ContextVar[int | None] = ContextVar("current_job_id", default=None)


class JobCancelled(Exception):
    """Job прерван пользователем."""


def _now() -> str:
    return utc_now_iso()


def is_cancel_requested(job_id: int) -> bool:
    job = get_job(job_id)
    if job is None:
        return True
    if job.get("status") == "cancelled":
        return True
    return bool(job.get("cancel_requested"))


def check_cancel() -> None:
    job_id = _current_job_id.get()
    if job_id is not None and is_cancel_requested(job_id):
        raise JobCancelled(f"job {job_id} cancelled")


@contextmanager
def job_context(job_id: int) -> Iterator[None]:
    token = _current_job_id.set(job_id)
    try:
        yield
    finally:
        _current_job_id.reset(token)


def request_cancel(job_id: int) -> tuple[bool, str]:
    """
    queued → сразу cancelled (worker пропустит сообщение из очереди).
    processing → cancel_requested=1, worker завершит на ближайшей точке.
    """
    job = get_job(job_id)
    if job is None:
        return False, "задача не найдена"
    status = job.get("status")
    if status == "queued":
        update_job(
            job_id,
            status="cancelled",
            cancel_requested=1,
            finished_at=_now(),
            progress_pct=None,
            progress_detail=None,
            error_message="прервано пользователем",
        )
        return True, "задача снята с очереди"
    if status == "processing":
        if job.get("cancel_requested"):
            return True, "прерывание уже запрошено"
        update_job(
            job_id,
            cancel_requested=1,
            progress_detail="Прерывание…",
        )
        return True, "ожидание прерывания worker"
    return False, "задачу нельзя прервать"


def sleep_cancellable(seconds: float, *, step: float = 0.25) -> None:
    """Sleep с проверкой cancel (MOCK AI и т.п.)."""
    if seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    while True:
        check_cancel()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(step, remaining))
