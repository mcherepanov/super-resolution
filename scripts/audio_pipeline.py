"""Цепочка обработки: ffmpeg-фильтры → enhance (опция) → экспорт."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import soundfile as sf

from ffmpeg_ops import (
    OUTPUT_SR,
    TARGET_SR,
    FfmpegError,
    afftdn_filter,
    apply_af_chain,
    build_eq_filter,
    compand_filter,
    decode_to_wav_48k,
    export_audio,
    resample_wav,
)
from job_cancel import check_cancel
from process_options import parse_options
from progress import JobProgress, make_enhance_callback

LOWPASS = os.environ.get("LOWPASS", "").lower() in ("1", "true", "yes")
MOCK_MODE = os.environ.get("MOCK_MODE", "").lower() in ("1", "true", "yes")


def _work_paths(dst: Path, job_id: int) -> tuple[Path, list[Path]]:
    base = dst.parent / f".work_{dst.stem}_{job_id}"
    temps: list[Path] = []
    return base, temps


def _step_path(base: Path, step: str) -> Path:
    return base.with_name(f"{base.name}_{step}.wav")


def _probe_duration(path: Path) -> tuple[float, int]:
    data, sr = sf.read(str(path), dtype="float32")
    dur = len(data) / sr if sr > 0 else 0.0
    return dur, sr


def _cleanup_temps(temps: list[Path], dst: Path) -> None:
    for t in temps:
        if t.exists() and t != dst:
            t.unlink(missing_ok=True)


def run_pipeline(
    job_id: int,
    src: Path,
    dst: Path,
    options_raw: str | dict[str, Any] | None,
    *,
    model: Any = None,
    device: Any = None,
    progress: JobProgress | None = None,
) -> tuple[float, int, int]:
    """
    Выполнить цепочку. Возвращает (duration_sec, input_sr, output_sr).
    """
    opts = parse_options(options_raw)
    if opts.get("enhance") and MOCK_MODE:
        raise RuntimeError("AI-улучшение недоступно в режиме «Только обработка»")

    dst.parent.mkdir(parents=True, exist_ok=True)
    base, temps = _work_paths(dst, job_id)
    current = _step_path(base, "48k")
    temps.append(current)

    try:
        check_cancel()
        if progress is not None:
            progress.set_step_progress(0.0, "Декодирование")
        decode_to_wav_48k(src, current)
        if progress is not None:
            progress.complete_step("Декодирование")

        step_idx = 0

        # Фильтры: явные блоки намеренно (не сворачивать в цикл при рефакторинге).
        # Порядок этапов, temp-файлы и отладка должны оставаться прозрачными.
        denoise = opts.get("denoise")
        if denoise == "afftdn":
            check_cancel()
            if progress is not None:
                progress.set_step_progress(0.0, "Фильтр: afftdn")
            nxt = _step_path(base, f"f{step_idx}")
            step_idx += 1
            af = afftdn_filter(opts["afftdn_nr"], opts["afftdn_nf"])
            apply_af_chain(current, nxt, af)
            temps.append(nxt)
            current = nxt
            if progress is not None:
                progress.complete_step("Фильтр: afftdn")
        elif denoise == "anlmdn":
            check_cancel()
            if progress is not None:
                progress.set_step_progress(0.0, "Фильтр: anlmdn")
            nxt = _step_path(base, f"f{step_idx}")
            step_idx += 1
            apply_af_chain(current, nxt, "anlmdn")
            temps.append(nxt)
            current = nxt
            if progress is not None:
                progress.complete_step("Фильтр: anlmdn")

        eq = opts.get("eq")
        if eq:
            check_cancel()
            if progress is not None:
                progress.set_step_progress(0.0, "Фильтр: EQ")
            nxt = _step_path(base, f"f{step_idx}")
            step_idx += 1
            af = build_eq_filter(eq, opts["highpass_hz"], opts["lowpass_hz"])
            apply_af_chain(current, nxt, af)
            temps.append(nxt)
            current = nxt
            if progress is not None:
                progress.complete_step("Фильтр: EQ")

        if opts.get("compand"):
            check_cancel()
            if progress is not None:
                progress.set_step_progress(0.0, "Фильтр: compand")
            nxt = _step_path(base, f"f{step_idx}")
            step_idx += 1
            af = compand_filter(opts["compand_intensity"])
            apply_af_chain(current, nxt, af)
            temps.append(nxt)
            current = nxt
            if progress is not None:
                progress.complete_step("Фильтр: compand")

        if opts.get("loudnorm"):
            check_cancel()
            if progress is not None:
                progress.set_step_progress(0.0, "Фильтр: loudnorm")
            nxt = _step_path(base, f"f{step_idx}")
            step_idx += 1
            apply_af_chain(current, nxt, "loudnorm")
            temps.append(nxt)
            current = nxt
            if progress is not None:
                progress.complete_step("Фильтр: loudnorm")

        out_sr = TARGET_SR
        pcm_for_export = current
        want_441 = opts.get("resample_441", True)
        export_sr = OUTPUT_SR if want_441 else TARGET_SR

        if opts.get("enhance"):
            if model is None or device is None:
                raise RuntimeError("enhance requested but model not loaded")
            from super_resolve import enhance_file

            enhanced_dst = _step_path(base, "enhanced")
            temps.append(enhanced_dst)
            check_cancel()
            if progress is not None:
                progress.set_step_progress(0.0, "AI")
            enhance_file(
                model, current, enhanced_dst,
                device=device,
                lowpass=bool(opts.get("enhance_lowpass", LOWPASS)),
                on_progress=make_enhance_callback(progress),
                output_sr=export_sr,
            )
            pcm_for_export = enhanced_dst
            out_sr = export_sr
            if progress is not None:
                progress.complete_step("AI · готово")
        elif want_441:
            check_cancel()
            if progress is not None:
                progress.set_step_progress(0.0, "Ресемпл 44.1 kHz")
            resampled = _step_path(base, "441")
            temps.append(resampled)
            resample_wav(current, resampled, OUTPUT_SR)
            pcm_for_export = resampled
            out_sr = OUTPUT_SR
            if progress is not None:
                progress.complete_step("Ресемпл 44.1 kHz")

        out_fmt = opts.get("output_format", "wav")
        check_cancel()
        if progress is not None:
            progress.set_step_progress(0.0, f"Экспорт {out_fmt}")
        if out_fmt == "wav":
            shutil.move(str(pcm_for_export), str(dst))
        else:
            export_audio(
                pcm_for_export,
                dst,
                out_fmt,
                mp3_bitrate=opts.get("mp3_bitrate", 320),
            )
        if progress is not None:
            progress.complete_step(f"Экспорт {out_fmt}")

        duration, input_sr = _probe_duration(src)
        _cleanup_temps(temps, dst)
        return duration, input_sr, out_sr
    except Exception:
        if dst.exists():
            dst.unlink(missing_ok=True)
        _cleanup_temps(temps, dst)
        raise
