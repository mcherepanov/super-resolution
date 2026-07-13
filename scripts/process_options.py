"""Парсинг и валидация опций обработки."""

from __future__ import annotations

import json
from typing import Any

DENOISE_OPS = frozenset({"afftdn", "anlmdn"})
EQ_OPS = frozenset({"highpass", "lowpass", "both"})
OUTPUT_FORMATS = frozenset({"wav", "mp3", "flac", "m4a"})
MP3_BITRATES = frozenset({128, 192, 256, 320})
MP3_BITRATE_DEFAULT = 320


def _parse_mp3_bitrate(val: Any) -> int:
    n = _clamp_int(val, 128, 320, MP3_BITRATE_DEFAULT)
    return n if n in MP3_BITRATES else MP3_BITRATE_DEFAULT


def _clamp_int(val: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def parse_options(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None or raw == "":
        data: dict[str, Any] = {}
    elif isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = dict(raw)

    denoise = data.get("denoise")
    if denoise in ("", "none", None):
        denoise = None
    elif denoise not in DENOISE_OPS:
        raise ValueError(f"invalid denoise: {denoise}")

    eq = data.get("eq")
    if eq in ("", "none", None):
        eq = None
    elif eq not in EQ_OPS:
        raise ValueError(f"invalid eq: {eq}")

    out_fmt = str(data.get("output_format", "wav")).lower()
    if out_fmt not in OUTPUT_FORMATS:
        raise ValueError(f"invalid output_format: {out_fmt}")

    return {
        "denoise": denoise,
        "afftdn_nr": _clamp_int(data.get("afftdn_nr"), 1, 30, 12),
        "afftdn_nf": _clamp_int(data.get("afftdn_nf"), -80, -20, -50),
        "eq": eq,
        "highpass_hz": _clamp_int(data.get("highpass_hz"), 20, 500, 80),
        "lowpass_hz": _clamp_int(data.get("lowpass_hz"), 1000, 20000, 10000),
        "compand": bool(data.get("compand")),
        "compand_intensity": _clamp_int(data.get("compand_intensity"), 0, 100, 50),
        "loudnorm": bool(data.get("loudnorm")),
        "enhance": bool(data.get("enhance")),
        "enhance_lowpass": bool(data.get("enhance_lowpass")),
        "resample_441": bool(data.get("resample_441", True)),
        "output_format": out_fmt,
        "mp3_bitrate": _parse_mp3_bitrate(data.get("mp3_bitrate", MP3_BITRATE_DEFAULT)),
    }


def has_transformation(opts: dict[str, Any], input_suffix: str) -> bool:
    if opts.get("enhance"):
        return True
    if opts.get("denoise"):
        return True
    if opts.get("eq"):
        return True
    if opts.get("compand"):
        return True
    if opts.get("loudnorm"):
        return True
    if opts.get("resample_441", True):
        return True
    in_ext = input_suffix.lower().lstrip(".")
    if opts.get("output_format", "wav") != in_ext:
        return True
    return False


def options_summary(opts: dict[str, Any]) -> str:
    parts: list[str] = []
    if opts.get("denoise"):
        parts.append(opts["denoise"])
    if opts.get("eq"):
        parts.append(opts["eq"])
    if opts.get("compand"):
        parts.append("compand")
    if opts.get("loudnorm"):
        parts.append("loudnorm")
    if opts.get("enhance"):
        parts.append("AI")
        if opts.get("enhance_lowpass"):
            parts.append("LP")
    if opts.get("resample_441", True):
        parts.append("44.1k")
    else:
        parts.append("48k")
    out_fmt = opts.get("output_format", "wav")
    if out_fmt == "mp3":
        parts.append(f"mp3 {opts.get('mp3_bitrate', MP3_BITRATE_DEFAULT)}k")
    else:
        parts.append(out_fmt)
    return ", ".join(parts) if parts else "—"


def job_options_summary(job: dict[str, Any]) -> str:
    """Одна строка «Обработка» как в веб-таблице jobs."""
    jt = job.get("job_type") or "process"
    if jt == "cue_split":
        try:
            opts = json.loads(job.get("options") or "{}")
            fmt = opts.get("split_format", "wav")
            return f"CUE split → {fmt}"
        except json.JSONDecodeError:
            return "CUE split"
    if jt == "cue_batch":
        return "CUE batch"
    raw = job.get("options")
    if not raw:
        return "—"
    try:
        return options_summary(parse_options(raw))
    except (json.JSONDecodeError, ValueError):
        return "—"


def _map_slider(val: Any, lo: int, hi: int, default_pos: int = 50) -> int:
    pos = _clamp_int(val, 0, 100, default_pos)
    return int(round(lo + (hi - lo) * pos / 100.0))


def options_from_form(form: dict[str, str]) -> dict[str, Any]:
    denoise = form.get("denoise") or None
    eq = form.get("eq") or None
    return {
        "denoise": denoise if denoise in DENOISE_OPS else None,
        "afftdn_nr": _map_slider(form.get("afftdn_slider"), 1, 30),
        "afftdn_nf": _map_slider(form.get("afftdn_nf_slider"), -80, -20),
        "eq": eq if eq in EQ_OPS else None,
        "highpass_hz": _clamp_int(form.get("highpass_hz"), 20, 500, 80),
        "lowpass_hz": _clamp_int(form.get("lowpass_hz"), 1000, 20000, 10000),
        "compand": form.get("compand") == "on",
        "compand_intensity": _clamp_int(form.get("compand_intensity"), 0, 100, 50),
        "loudnorm": form.get("loudnorm") == "on",
        "enhance": form.get("enhance") == "on",
        "enhance_lowpass": (
            form.get("enhance_lowpass") == "on" and form.get("enhance") == "on"
        ),
        "resample_441": form.get("resample_441", "on") in ("on", "1", "true"),
        "output_format": form.get("output_format", "wav"),
        "mp3_bitrate": _parse_mp3_bitrate(form.get("mp3_bitrate", str(MP3_BITRATE_DEFAULT))),
    }
