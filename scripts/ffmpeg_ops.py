"""FFmpeg: декодирование, фильтры, экспорт."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

TARGET_SR = 48_000
OUTPUT_SR = 44_100

INPUT_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".opus", ".ape", ".m4a"}
OUTPUT_FORMATS = {"wav", "mp3", "flac", "m4a"}

COMPAND_POINTS = "-70/-60|-60/-20|-20/-14|-14/-10|-10/-5"


class FfmpegError(RuntimeError):
    pass


def _run(cmd: list[str]) -> None:
    from job_cancel import JobCancelled, check_cancel

    try:
        check_cancel()
    except JobCancelled:
        raise

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while proc.poll() is None:
            check_cancel()
            time.sleep(0.25)
        stdout, stderr = proc.communicate(timeout=1)
    except JobCancelled:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise

    if proc.returncode != 0:
        tail = (stderr or stdout or "")[-2000:]
        raise FfmpegError(tail.strip() or f"ffmpeg failed: {' '.join(cmd)}")


def decode_to_wav_48k(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-ar", str(TARGET_SR),
        "-ac", "2",
        "-sample_fmt", "s16",
        str(dst), "-y",
    ])


def resample_wav(src: Path, dst: Path, sample_rate: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-ar", str(sample_rate),
        str(dst), "-y",
    ])


def apply_af_chain(src: Path, dst: Path, af: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-af", af,
        str(dst), "-y",
    ])


def afftdn_filter(nr: int, nf: int) -> str:
    return f"afftdn=nr={nr}:nf={nf}"


def compand_filter(intensity: int) -> str:
    """intensity 0–100, 50 ≈ классический пиковый компрессор."""
    p = max(0, min(100, intensity)) / 100.0
    attack = 0.5 - 0.4 * p
    decay = attack * 20.0
    knee = 1.0 + 11.0 * (1.0 - p)
    return (
        f"compand={attack:.2f}|{attack:.2f}:{decay:.1f}|{decay:.1f}"
        f":{COMPAND_POINTS}:{knee:.2f}:0:-90:0.1"
    )


def build_eq_filter(eq: str, highpass_hz: int, lowpass_hz: int) -> str:
    parts: list[str] = []
    if eq in ("highpass", "both"):
        parts.append(f"highpass=f={highpass_hz}")
    if eq in ("lowpass", "both"):
        parts.append(f"lowpass=f={lowpass_hz}")
    return ",".join(parts)


def export_audio(
    src: Path,
    dst: Path,
    fmt: str,
    *,
    mp3_bitrate: int = 320,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = dst.suffix.lower().lstrip(".")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src), "-y"]
    if fmt == "wav" or ext == "wav":
        cmd += ["-c:a", "pcm_s16le"]
    elif fmt == "flac":
        cmd += ["-c:a", "flac"]
    elif fmt == "mp3":
        br = mp3_bitrate if mp3_bitrate in (128, 192, 256, 320) else 320
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{br}k"]
    elif fmt == "m4a":
        cmd += ["-c:a", "aac", "-b:a", "256k"]
    else:
        raise ValueError(f"unsupported output format: {fmt}")
    cmd.append(str(dst))
    _run(cmd)
