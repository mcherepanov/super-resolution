"""Цепочка обработки: ffmpeg-фильтры → enhance (опция) → экспорт."""

from __future__ import annotations

import os
import shutil
import time
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
from process_options import parse_options

LOWPASS = os.environ.get("LOWPASS", "").lower() in ("1", "true", "yes")
MOCK_MODE = os.environ.get("MOCK_MODE", "").lower() in ("1", "true", "yes")
MOCK_DELAY_SEC = float(os.environ.get("MOCK_DELAY_SEC", "3"))


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


def _mock_enhance_delay(duration: float) -> None:
    delay = min(max(duration * 0.05, MOCK_DELAY_SEC * 0.5), MOCK_DELAY_SEC * 3)
    print(f"  [MOCK] AI sleep {delay:.1f}s ...")
    time.sleep(delay)


def run_pipeline(
    job_id: int,
    src: Path,
    dst: Path,
    options_raw: str | dict[str, Any] | None,
    *,
    model: Any = None,
    device: Any = None,
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

    decode_to_wav_48k(src, current)
    step_idx = 0

    denoise = opts.get("denoise")
    if denoise == "afftdn":
        nxt = _step_path(base, f"f{step_idx}")
        step_idx += 1
        af = afftdn_filter(opts["afftdn_nr"], opts["afftdn_nf"])
        apply_af_chain(current, nxt, af)
        temps.append(nxt)
        current = nxt
    elif denoise == "anlmdn":
        nxt = _step_path(base, f"f{step_idx}")
        step_idx += 1
        apply_af_chain(current, nxt, "anlmdn")
        temps.append(nxt)
        current = nxt

    eq = opts.get("eq")
    if eq:
        nxt = _step_path(base, f"f{step_idx}")
        step_idx += 1
        af = build_eq_filter(eq, opts["highpass_hz"], opts["lowpass_hz"])
        apply_af_chain(current, nxt, af)
        temps.append(nxt)
        current = nxt

    if opts.get("compand"):
        nxt = _step_path(base, f"f{step_idx}")
        step_idx += 1
        af = compand_filter(opts["compand_intensity"])
        apply_af_chain(current, nxt, af)
        temps.append(nxt)
        current = nxt

    if opts.get("loudnorm"):
        nxt = _step_path(base, f"f{step_idx}")
        step_idx += 1
        apply_af_chain(current, nxt, "loudnorm")
        temps.append(nxt)
        current = nxt

    out_sr = TARGET_SR
    pcm_for_export = current

    if opts.get("enhance"):
        if model is None or device is None:
            raise RuntimeError("enhance requested but model not loaded")
        from super_resolve import enhance_file

        enhanced_dst = _step_path(base, "enhanced")
        temps.append(enhanced_dst)
        dur_hint, _ = _probe_duration(current)
        if MOCK_MODE:
            _mock_enhance_delay(dur_hint)
        enhance_file(model, current, enhanced_dst, device=device, lowpass=LOWPASS)
        pcm_for_export = enhanced_dst
        out_sr = OUTPUT_SR
    elif opts.get("resample_441", True):
        resampled = _step_path(base, "441")
        temps.append(resampled)
        resample_wav(current, resampled, OUTPUT_SR)
        pcm_for_export = resampled
        out_sr = OUTPUT_SR

    out_fmt = opts.get("output_format", "wav")
    if out_fmt == "wav":
        shutil.move(str(pcm_for_export), str(dst))
    else:
        export_audio(pcm_for_export, dst, out_fmt)

    duration, input_sr = _probe_duration(src)
    for t in temps:
        if t.exists() and t != dst:
            t.unlink(missing_ok=True)

    return duration, input_sr, out_sr
