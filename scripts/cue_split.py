"""Нарезка аудио по CUE sheet."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cue_sheet import (
    CueFileEntry,
    CueSheet,
    parse_cue,
    safe_track_name,
    split_output_dir,
)
from ffmpeg_ops import FfmpegError, _run, export_audio

SPLIT_FORMATS = frozenset({"wav", "flac", "mp3"})


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise FfmpegError((proc.stderr or "ffprobe failed")[-2000:])
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise FfmpegError(f"cannot read duration: {path}") from exc


def _extract_segment(
    src: Path,
    dst: Path,
    start: float,
    end: float,
    fmt: str,
) -> None:
    duration = max(0.01, end - start)
    temp = dst.with_suffix(".tmp.wav")
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.6f}",
        "-i", str(src),
        "-t", f"{duration:.6f}",
        "-ar", "48000",
        "-ac", "2",
        str(temp), "-y",
    ])
    if fmt == "wav":
        temp.replace(dst)
    else:
        export_audio(temp, dst, fmt)
        temp.unlink(missing_ok=True)


def split_file_by_cue(
    entry: CueFileEntry,
    out_dir: Path,
    fmt: str,
) -> tuple[int, float]:
    from job_cancel import JobCancelled, check_cancel

    if entry.resolved is None:
        raise FfmpegError(f"audio not found: {entry.cue_name}")
    if not entry.tracks:
        raise FfmpegError(f"no tracks in cue for {entry.cue_name}")

    src = entry.resolved
    total_dur = probe_duration(src)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    audio_dur = 0.0
    written_paths: list[Path] = []
    try:
        for track in entry.tracks:
            check_cancel()
            start = track.index01_sec
            end = track.end_sec if track.end_sec is not None else total_dur
            if end <= start:
                continue
            name = safe_track_name(track.number, track.title, track.performer)
            dst = out_dir / f"{name}.{fmt}"
            _extract_segment(src, dst, start, end, fmt)
            written_paths.append(dst)
            written += 1
            audio_dur += end - start
    except JobCancelled:
        for path in written_paths:
            path.unlink(missing_ok=True)
        raise

    return written, audio_dur


def run_cue_split(
    cue_path: Path,
    *,
    audio_path: Path | None = None,
    split_format: str = "wav",
) -> tuple[int, float, Path]:
    fmt = split_format.lower()
    if fmt not in SPLIT_FORMATS:
        raise ValueError(f"unsupported split format: {fmt}")

    sheet = parse_cue(cue_path, input_dir=cue_path.parent)
    if sheet.is_multi_file:
        raise FfmpegError("split поддерживает только CUE с одним аудиофайлом")

    entry = sheet.files[0]
    if audio_path is not None:
        entry = _find_entry(sheet, audio_path)
    if entry.resolved is None:
        raise FfmpegError(f"audio not found for {cue_path.name}")

    out_dir = split_output_dir(entry.resolved)
    count, dur = split_file_by_cue(entry, out_dir, fmt)
    return count, dur, out_dir


def _find_entry(sheet: CueSheet, audio_path: Path) -> CueFileEntry:
    audio_path = audio_path.resolve()
    for entry in sheet.files:
        if entry.resolved and entry.resolved.resolve() == audio_path:
            return entry
    raise FfmpegError(f"audio {audio_path.name} not in cue")


def run_cue_batch(
    cue_path: Path,
    process_fn,
) -> tuple[int, int, list[str]]:
    """process_fn(audio_path, track_index) -> None; ошибки собираются."""
    from job_cancel import JobCancelled

    sheet = parse_cue(cue_path, input_dir=cue_path.parent)
    ok_count = 0
    errors: list[str] = []
    total = len(sheet.files)
    track_index = 0

    for entry in sheet.files:
        if entry.resolved is None:
            errors.append(f"{entry.cue_name}: not found")
            continue
        track_index += 1
        try:
            process_fn(entry.resolved, track_index)
            ok_count += 1
        except JobCancelled:
            raise
        except Exception as exc:
            errors.append(f"{entry.resolved.name}: {exc}")

    return ok_count, total, errors
