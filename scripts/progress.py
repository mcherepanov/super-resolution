"""Прогресс обработки: консоль + SQLite (throttle)."""

from __future__ import annotations

import sys
import time
from typing import Any, Callable

from db import update_job

BAR_LEN = 40
DB_THROTTLE_SEC = 1.0

WEIGHT_DECODE = 0.05
WEIGHT_FILTER = 0.03
WEIGHT_ENHANCE = 0.70
WEIGHT_EXPORT = 0.07
WEIGHT_RESAMPLE = 0.05

_db_state: dict[int, dict[str, Any]] = {}


def clear_job_progress_state(job_id: int) -> None:
    _db_state.pop(job_id, None)


def update_job_progress(
    job_id: int,
    pct: float,
    detail: str,
    *,
    force: bool = False,
) -> None:
    pct = max(0.0, min(100.0, pct))
    state = _db_state.setdefault(job_id, {"t": 0.0, "detail": "", "pct": -1.0})
    now = time.monotonic()
    changed_detail = detail != state["detail"]
    if not force:
        if now - state["t"] < DB_THROTTLE_SEC and not changed_detail:
            if abs(pct - state["pct"]) < 1.0 and pct < 99.9:
                return
    update_job(job_id, progress_pct=pct, progress_detail=detail)
    state["t"] = now
    state["detail"] = detail
    state["pct"] = pct


class ProgressReporter:
    """Консольный бар + опционально запись в БД."""

    def __init__(self, job_id: int | None = None, *, prefix: str = "") -> None:
        self.job_id = job_id
        self.prefix = prefix
        self._last_line = ""
        self._tty = sys.stderr.isatty()

    def report(self, pct: float, detail: str, *, force_db: bool = False) -> None:
        pct = max(0.0, min(100.0, pct))
        self._console(pct, detail)
        if self.job_id is not None:
            update_job_progress(self.job_id, pct, detail, force=force_db)

    def _console(self, pct: float, detail: str) -> None:
        filled = int(BAR_LEN * pct / 100)
        bar = "█" * filled + "░" * (BAR_LEN - filled)
        head = f"{self.prefix}| " if self.prefix else "  "
        line = f"{head}[{bar}] {pct:5.1f}% | {detail}"
        if self._tty:
            print(f"\r{line}", end="", file=sys.stderr, flush=True)
            self._last_line = line
            return
        if line != self._last_line:
            print(line, file=sys.stderr, flush=True)
            self._last_line = line

    def finish_line(self) -> None:
        if self._tty and self._last_line:
            print(file=sys.stderr, flush=True)
            self._last_line = ""


def _normalize_weights(stages: list[tuple[str, float]]) -> list[tuple[str, float]]:
    total = sum(w for _, w in stages)
    if total <= 0:
        return stages
    return [(name, w / total) for name, w in stages]


class JobProgress:
    """Доли этапов пайплайна → общий %."""

    def __init__(
        self,
        reporter: ProgressReporter | None,
        *,
        has_enhance: bool,
        filter_labels: list[str],
    ) -> None:
        self.reporter = reporter
        stages: list[tuple[str, float]] = [("Декодирование", WEIGHT_DECODE)]
        for label in filter_labels:
            stages.append((f"Фильтр: {label}", WEIGHT_FILTER))
        if has_enhance:
            stages.append(("AI", WEIGHT_ENHANCE))
        else:
            stages.append(("Ресемпл 44.1", WEIGHT_RESAMPLE))
        stages.append(("Экспорт", WEIGHT_EXPORT))
        self._stages = _normalize_weights(stages)
        self._cursor = 0
        self._base = 0.0
        self._batch_track = 0
        self._batch_total = 0
        self._ai_chunks_per_channel = 0

    def set_batch(self, track_index: int, track_total: int) -> None:
        self._batch_track = track_index
        self._batch_total = track_total
        self._cursor = 0
        self._base = 0.0
        self._ai_chunks_per_channel = 0

    def _stage_weight(self) -> float:
        if self._cursor >= len(self._stages):
            return 0.0
        return self._stages[self._cursor][1]

    def _emit(self, fraction: float, detail: str, *, force_db: bool = False) -> None:
        if self.reporter is None:
            return
        pct = self._composite_pct(fraction) * 100.0
        if self._batch_total > 0:
            detail = f"Трек {self._batch_track}/{self._batch_total} · {detail}"
        self.reporter.report(pct, detail, force_db=force_db)

    def _composite_pct(self, track_fraction: float) -> float:
        track_fraction = max(0.0, min(1.0, track_fraction))
        if self._batch_total > 0:
            return ((self._batch_track - 1) + track_fraction) / self._batch_total
        return track_fraction

    def set_step_progress(self, sub: float, detail: str) -> None:
        """sub: 0..1 внутри текущего этапа."""
        fraction = self._base + self._stage_weight() * max(0.0, min(1.0, sub))
        self._emit(fraction, detail)

    def complete_step(self, detail: str | None = None) -> None:
        if self._cursor >= len(self._stages):
            return
        name, weight = self._stages[self._cursor]
        self._base += weight
        self._cursor += 1
        label = detail or name
        self._emit(self._base, label, force_db=True)

    def stage_enhance_chunk(
        self,
        channel: str,
        current: int,
        total: int,
        elapsed: float,
    ) -> None:
        if total <= 0:
            return
        if channel == "L":
            self._ai_chunks_per_channel = total
            sub = (current / total) * 0.5
            overall_cur = current
            overall_tot = total * 2
            detail = f"AI · {overall_cur}/{overall_tot} · L {current}/{total}"
        elif channel == "R":
            prev = self._ai_chunks_per_channel or total
            sub = 0.5 + (current / total) * 0.5
            overall_cur = prev + current
            overall_tot = prev + total
            detail = f"AI · {overall_cur}/{overall_tot} · R {current}/{total}"
        else:
            sub = current / total
            detail = f"AI · {channel} · {current}/{total}"

        eta_str = ""
        if current > 1 and elapsed > 0:
            rate = (current - 1) / elapsed
            if rate > 0:
                if channel == "L" and self._ai_chunks_per_channel > 0:
                    remaining = (total - current) + total
                else:
                    remaining = total - current
                eta_str = f" · ETA {remaining / rate:.0f}s"
        self.set_step_progress(sub, f"{detail}{eta_str}")


def build_job_progress(
    reporter: ProgressReporter | None,
    opts: dict[str, Any],
) -> JobProgress:
    filters: list[str] = []
    denoise = opts.get("denoise")
    if denoise:
        filters.append(str(denoise))
    if opts.get("eq"):
        filters.append("EQ")
    if opts.get("compand"):
        filters.append("compand")
    if opts.get("loudnorm"):
        filters.append("loudnorm")
    return JobProgress(
        reporter,
        has_enhance=bool(opts.get("enhance")),
        filter_labels=filters,
    )


def make_enhance_callback(
    job_progress: JobProgress | None,
) -> Callable[[int, int, str, float], None] | None:
    if job_progress is None:
        return None

    from job_cancel import check_cancel

    def _cb(current: int, total: int, channel: str, elapsed: float) -> None:
        check_cancel()
        job_progress.stage_enhance_chunk(channel, current, total, elapsed)

    return _cb
