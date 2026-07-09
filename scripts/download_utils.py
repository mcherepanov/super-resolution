"""Подготовка скачивания и очистка артефактов job."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from cue_sheet import parse_cue


@dataclass
class DownloadPayload:
    path: Path
    filename: str
    temp_zip: Path | None = None
    delete_files: list[Path] = field(default_factory=list)
    delete_dirs: list[Path] = field(default_factory=list)


class DownloadError(Exception):
    pass


def _safe_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _zip_directory(src_dir: Path, arc_stem: str) -> Path:
    if not src_dir.is_dir():
        raise DownloadError(f"папка не найдена: {src_dir}")
    files = [p for p in src_dir.rglob("*") if p.is_file()]
    if not files:
        raise DownloadError(f"папка пуста: {src_dir}")

    tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(files):
            zf.write(f, arcname=str(Path(arc_stem) / f.relative_to(src_dir)))
    return tmp


def _zip_files(files: list[Path], arc_stem: str) -> Path:
    existing = [p for p in files if p.is_file()]
    if not existing:
        raise DownloadError("нет файлов для архива")
    tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(existing):
            zf.write(f, arcname=f"{arc_stem}/{f.name}")
    return tmp


def _batch_output_files(job: dict, output_dir: Path) -> list[Path]:
    opts = job.get("options") or "{}"
    if isinstance(opts, str):
        opts = json.loads(opts)
    stored = opts.get("output_files")
    if stored:
        return [Path(p) for p in stored if Path(p).is_file()]

    cue_path = Path(job["input_path"])
    pipeline = opts.get("pipeline", {})
    out_fmt = pipeline.get("output_format", "wav")
    sheet = parse_cue(cue_path, input_dir=cue_path.parent)
    paths: list[Path] = []
    for entry in sheet.files:
        if entry.resolved:
            candidate = output_dir / f"{entry.resolved.stem}.{out_fmt}"
            if candidate.is_file():
                paths.append(candidate)
    return paths


def prepare_download(
    job: dict,
    *,
    input_dir: Path,
    output_dir: Path,
) -> DownloadPayload:
    job_type = job.get("job_type") or "process"
    stem = Path(job["filename"]).stem

    if job_type == "cue_split":
        folder = Path(job["output_path"])
        if not _safe_under(folder, input_dir):
            raise DownloadError("недопустимый путь")
        zip_path = _zip_directory(folder, folder.name)
        return DownloadPayload(
            path=zip_path,
            filename=f"{folder.name}.zip",
            temp_zip=zip_path,
            delete_dirs=[folder],
        )

    if job_type == "cue_batch":
        files = _batch_output_files(job, output_dir)
        for f in files:
            if not _safe_under(f, output_dir):
                raise DownloadError("недопустимый путь")
        zip_path = _zip_files(files, stem)
        marker = Path(job["output_path"])
        delete_files = list(files)
        if marker.is_file():
            delete_files.append(marker)
        return DownloadPayload(
            path=zip_path,
            filename=f"{stem}_batch.zip",
            temp_zip=zip_path,
            delete_files=delete_files,
        )

    path = Path(job["output_path"])
    if not path.is_file():
        raise DownloadError("файл не найден")
    if not _safe_under(path, output_dir):
        raise DownloadError("недопустимый путь")
    out_fmt = job.get("output_format") or path.suffix.lstrip(".")
    return DownloadPayload(
        path=path,
        filename=f"{stem}.{out_fmt}",
        delete_files=[path],
    )


def cleanup_after_download(
    payload: DownloadPayload,
    *,
    delete_artifacts: bool,
    delete_job_fn,
    job_id: int,
) -> None:
    if payload.temp_zip and payload.temp_zip.exists():
        payload.temp_zip.unlink(missing_ok=True)

    if not delete_artifacts:
        return

    for f in payload.delete_files:
        if f.exists() and f.is_file():
            f.unlink(missing_ok=True)
    for d in payload.delete_dirs:
        if d.exists() and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    delete_job_fn(job_id)
