"""JSON/multipart API для мобильного клиента (Android)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cue_sheet import cue_info_dict, validate_cue
from db import create_job, get_job, has_active_job, has_active_job_for_input, list_jobs
from download_utils import DownloadError, job_download_artifacts, prepare_download
from ffmpeg_ops import INPUT_EXTENSIONS
from messaging import publish_job
from process_options import has_transformation, job_options_summary, parse_options

INPUT_DIR = Path(os.environ.get("INPUT_DIR", "/app/input"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/output"))
AUDIO_EXTENSIONS = INPUT_EXTENSIONS
CUE_EXTENSION = ".cue"

MOBILE_PRESETS: dict[str, dict[str, Any]] = {
    "ai_flac": {
        "enhance": True,
        "resample_441": True,
        "output_format": "flac",
    },
}


def _output_path_for(input_path: Path, output_format: str) -> Path:
    stem = input_path.stem
    ext = output_format if output_format.startswith(".") else f".{output_format}"
    return OUTPUT_DIR / f"{stem}{ext}"


def _enqueue_process(input_path: Path, options: dict[str, Any]) -> int | None:
    input_path = input_path.resolve()
    opts = parse_options(options)
    out_fmt = opts["output_format"]
    output_path = _output_path_for(input_path, out_fmt)
    str_in = str(input_path)
    str_out = str(output_path)

    if not has_transformation(opts, input_path.suffix):
        return None

    if has_active_job(str_in, str_out):
        return None

    job_id = create_job(
        input_path.name,
        str_in,
        str_out,
        options=opts,
        output_format=out_fmt,
        job_type="process",
    )
    publish_job(job_id)
    return job_id


def resolve_process_options(
    *,
    preset: str | None,
    options: dict[str, Any] | None,
    enhance_available: bool,
) -> dict[str, Any]:
    if preset:
        if preset not in MOBILE_PRESETS:
            raise ValueError(f"unknown preset: {preset}")
        raw = dict(MOBILE_PRESETS[preset])
    elif options:
        raw = dict(options)
    else:
        raise ValueError("preset or options required")

    parsed = parse_options(raw)
    if parsed.get("enhance") and not enhance_available:
        raise ValueError("AI-улучшение недоступно")
    return parsed


def list_input_files() -> list[dict[str, Any]]:
    if not INPUT_DIR.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    ):
        resolved = str(path.resolve())
        stat = path.stat()
        files.append({
            "name": path.name,
            "size_bytes": stat.st_size,
            "busy": has_active_job_for_input(resolved),
        })
    return files


async def upload_files(uploads: list[tuple[str, bytes]]) -> dict[str, Any]:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved_audio = 0
    errors: list[str] = []
    saved_names: list[str] = []
    cue_info: dict | None = None

    for name, content in uploads:
        safe_name = Path(name).name
        suffix = Path(safe_name).suffix.lower()
        dest = INPUT_DIR / safe_name

        if suffix == CUE_EXTENSION:
            dest.write_bytes(content)
            ok, missing, sheet = validate_cue(dest, INPUT_DIR)
            if not ok:
                errors.append(f"CUE {safe_name}: не найдены — {', '.join(missing)}")
            elif sheet is not None:
                cue_info = cue_info_dict(sheet)
                saved_names.append(safe_name)
            continue

        if suffix not in AUDIO_EXTENSIONS:
            errors.append(f"{safe_name}: неподдерживаемый формат")
            continue

        dest.write_bytes(content)
        saved_audio += 1
        saved_names.append(safe_name)

    return {
        "saved_audio": saved_audio,
        "saved_names": saved_names,
        "errors": errors,
        "cue_info": cue_info,
    }


def enqueue_process_filenames(
    filenames: list[str],
    options: dict[str, Any],
) -> dict[str, Any]:
    if not filenames:
        raise ValueError("filenames required")

    queued: list[int] = []
    skipped_missing: list[str] = []
    skipped_noop: list[str] = []
    skipped_busy: list[str] = []

    for raw in filenames:
        name = Path(raw).name
        path = INPUT_DIR / name
        if not path.is_file():
            skipped_missing.append(name)
            continue
        if not has_transformation(options, path.suffix):
            skipped_noop.append(name)
            continue
        job_id = _enqueue_process(path, options)
        if job_id is None:
            skipped_busy.append(name)
        else:
            queued.append(job_id)

    return {
        "queued": len(queued),
        "job_ids": queued,
        "skipped_missing": skipped_missing,
        "skipped_noop": skipped_noop,
        "skipped_busy": skipped_busy,
    }


def delete_input_file(filename: str) -> dict[str, Any]:
    name = Path(filename).name
    path = (INPUT_DIR / name).resolve()
    if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("file not found")
    if path.parent.resolve() != INPUT_DIR.resolve():
        raise ValueError("invalid path")
    if has_active_job_for_input(str(path)):
        raise ValueError("file busy")
    path.unlink()
    return {"deleted": name}


def list_ready_jobs() -> list[dict[str, Any]]:
    ready: list[dict[str, Any]] = []
    for job in list_jobs():
        if job.get("status") != "done":
            continue
        try:
            _entries, _df, _dd, download_filename = job_download_artifacts(
                job, input_dir=INPUT_DIR, output_dir=OUTPUT_DIR,
            )
        except DownloadError:
            continue
        ready.append({
            "id": job["id"],
            "filename": job["filename"],
            "output_format": job.get("output_format"),
            "download_filename": download_filename,
            "finished_at": job.get("finished_at"),
            "options_summary": job_options_summary(job),
        })
    return ready


def get_job_download(job_id: int):
    job = get_job(job_id)
    if job is None:
        raise DownloadError("job not found")
    if job.get("status") != "done":
        raise DownloadError("job not ready")
    return job, prepare_download(job, input_dir=INPUT_DIR, output_dir=OUTPUT_DIR)
