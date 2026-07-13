"""Web UI: upload, очередь, история, CUE."""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cue_sheet import (  # noqa: E402
    cue_info_dict,
    split_output_dir,
    validate_cue,
)
from db import (  # noqa: E402
    create_job,
    delete_job,
    get_job,
    has_active_job,
    has_active_job_for_input,
    init_db,
    list_jobs,
)
from job_cancel import request_cancel  # noqa: E402
from download_utils import (  # noqa: E402
    DownloadError,
    cleanup_after_batch_download,
    cleanup_after_download,
    count_ready_downloads,
    prepare_batch_download,
    prepare_download,
)
from mobile_status import build_mobile_status  # noqa: E402
from mobile_api import (  # noqa: E402
    delete_input_file as mobile_delete_input_file,
    enqueue_process_filenames,
    get_job_download,
    list_input_files as mobile_list_input_files,
    list_ready_jobs as mobile_list_ready_jobs,
    resolve_process_options,
    upload_files as mobile_upload_files,
)
from ffmpeg_ops import INPUT_EXTENSIONS  # noqa: E402
from messaging import publish_job  # noqa: E402
from process_options import (  # noqa: E402
    has_transformation,
    job_options_summary,
    options_from_form,
    options_summary,
    parse_options,
)

APP_DIR = Path(__file__).resolve().parent
INPUT_DIR = Path(os.environ.get("INPUT_DIR", "/app/input"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/output"))
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in ("1", "true", "yes")


def _enhance_available() -> bool:
    """UI: AI доступен если ENHANCE_AVAILABLE=1 или MOCK_MODE=0."""
    explicit = _env_bool("ENHANCE_AVAILABLE")
    if explicit is not None:
        return explicit
    mock = _env_bool("MOCK_MODE")
    if mock is not None:
        return not mock
    return False


ENHANCE_AVAILABLE = _enhance_available()

AUDIO_EXTENSIONS = INPUT_EXTENSIONS
CUE_EXTENSION = ".cue"

app = FastAPI(title="Super Resolution")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
security = HTTPBasic(auto_error=False)


def verify_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not APP_PASSWORD:
        return
    if credentials is None:
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic"})
    ok_user = secrets.compare_digest(credentials.username.encode(), b"admin")
    ok_pass = secrets.compare_digest(credentials.password.encode(), APP_PASSWORD.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic"})


def _rabbit_publish(job_id: int) -> None:
    publish_job(job_id)


def _output_path_for(input_path: Path, output_format: str) -> Path:
    stem = input_path.stem
    ext = output_format if output_format.startswith(".") else f".{output_format}"
    return OUTPUT_DIR / f"{stem}{ext}"


def _list_input_files() -> list[dict[str, str | bool]]:
    if not INPUT_DIR.exists():
        return []
    files: list[dict[str, str | bool]] = []
    for p in sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    ):
        resolved = str(p.resolve())
        files.append({
            "name": p.name,
            "delete_busy": has_active_job_for_input(resolved),
        })
    return files


def _list_cue_sheets() -> list[dict]:
    if not INPUT_DIR.exists():
        return []
    sheets: list[dict] = []
    for cue in sorted(INPUT_DIR.glob("*.cue")):
        ok, missing, sheet = validate_cue(cue, INPUT_DIR)
        info: dict = {"name": cue.name, "ok": ok, "missing": missing}
        if sheet is not None:
            info.update(cue_info_dict(sheet))
        sheets.append(info)
    return sheets


def _panel_ctx(request: Request, **extra) -> dict:
    return {
        "request": request,
        "input_files": _list_input_files(),
        "cue_sheets": _list_cue_sheets(),
        "enhance_available": ENHANCE_AVAILABLE,
        **extra,
    }


def _pipeline_form_dict(
    *,
    denoise: str,
    eq: str,
    highpass_hz: str,
    lowpass_hz: str,
    compand: str,
    compand_intensity: str,
    loudnorm: str,
    enhance: str,
    enhance_lowpass: str,
    resample_441: str,
    afftdn_slider: str,
    afftdn_nf_slider: str,
    output_format: str,
    mp3_bitrate: str,
) -> dict:
    return options_from_form({
        "denoise": denoise,
        "eq": eq,
        "highpass_hz": highpass_hz,
        "lowpass_hz": lowpass_hz,
        "compand": compand,
        "compand_intensity": compand_intensity,
        "loudnorm": loudnorm,
        "enhance": enhance if ENHANCE_AVAILABLE else "",
        "enhance_lowpass": enhance_lowpass if ENHANCE_AVAILABLE else "",
        "resample_441": resample_441,
        "afftdn_slider": afftdn_slider,
        "afftdn_nf_slider": afftdn_nf_slider,
        "output_format": output_format,
        "mp3_bitrate": mp3_bitrate,
    })


def _enqueue_process(input_path: Path, options: dict) -> int | None:
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
        input_path.name, str_in, str_out,
        options=opts, output_format=out_fmt, job_type="process",
    )
    _rabbit_publish(job_id)
    return job_id


def _enqueue_cue_split(cue_path: Path, split_format: str) -> int | None:
    ok, missing, sheet = validate_cue(cue_path, INPUT_DIR)
    if not ok or sheet is None:
        return None
    if sheet.is_multi_file:
        return None
    audio = sheet.files[0].resolved
    if audio is None:
        return None
    out_dir = split_output_dir(audio)
    str_cue = str(cue_path.resolve())
    str_out = str(out_dir.resolve())
    if has_active_job(str_cue, str_out):
        return None
    job_id = create_job(
        cue_path.name,
        str_cue,
        str_out,
        options={"split_format": split_format, "audio_path": str(audio)},
        output_format=split_format,
        job_type="cue_split",
    )
    _rabbit_publish(job_id)
    return job_id


def _enqueue_cue_batch(cue_path: Path, pipeline: dict) -> int | None:
    ok, _missing, sheet = validate_cue(cue_path, INPUT_DIR)
    if not ok or sheet is None or not sheet.is_multi_file:
        return None
    str_cue = str(cue_path.resolve())
    marker = str((OUTPUT_DIR / f"_batch_{cue_path.stem}").resolve())
    if has_active_job(str_cue, marker):
        return None
    job_id = create_job(
        cue_path.name,
        str_cue,
        marker,
        options={"pipeline": pipeline},
        output_format=pipeline.get("output_format", "wav"),
        job_type="cue_batch",
    )
    _rabbit_publish(job_id)
    return job_id


def _job_options_summary(job: dict) -> str:
    return job_options_summary(job)


@app.on_event("startup")
def startup() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    init_db()


templates.env.globals["job_options_summary"] = _job_options_summary
templates.env.globals["enhance_available"] = ENHANCE_AVAILABLE
templates.env.globals["ready_download_count"] = lambda: count_ready_downloads(
    list_jobs(), input_dir=INPUT_DIR, output_dir=OUTPUT_DIR,
)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: None = Depends(verify_auth)) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        _panel_ctx(request, jobs=list_jobs()),
    )


@app.get("/jobs/partial", response_class=HTMLResponse)
def jobs_partial(request: Request, _: None = Depends(verify_auth)) -> HTMLResponse:
    return templates.TemplateResponse(
        "jobs_table.html",
        {"request": request, "jobs": list_jobs()},
    )


@app.get("/api/mobile-status")
def api_mobile_status(_: None = Depends(verify_auth)) -> dict:
    """JSON для Android-клиента: очередь, worker, статистика за сегодня."""
    try:
        return build_mobile_status()
    except Exception as exc:
        return {
            "status": "error",
            "timestamp": int(time.time()),
            "error_message": str(exc)[:500],
        }


class MobileProcessRequest(BaseModel):
    filenames: list[str] = Field(min_length=1)
    preset: str | None = None
    options: dict | None = None


@app.get("/api/input/files")
def api_input_files(_: None = Depends(verify_auth)) -> dict:
    return {"status": "ok", "files": mobile_list_input_files()}


@app.post("/api/input/upload")
async def api_input_upload(
    files: list[UploadFile] = File(...),
    _: None = Depends(verify_auth),
) -> dict:
    uploads: list[tuple[str, bytes]] = []
    for uf in files:
        if not uf.filename:
            continue
        uploads.append((uf.filename, await uf.read()))
    if not uploads:
        raise HTTPException(400, "no files")
    result = await mobile_upload_files(uploads)
    return {"status": "ok", **result}


@app.delete("/api/input/files/{filename}")
def api_delete_input_file(filename: str, _: None = Depends(verify_auth)) -> dict:
    try:
        result = mobile_delete_input_file(filename)
    except ValueError as exc:
        msg = str(exc)
        if msg == "file not found":
            raise HTTPException(404, msg) from exc
        if msg == "file busy":
            raise HTTPException(409, "файл в очереди или обрабатывается") from exc
        raise HTTPException(400, msg) from exc
    return {"status": "ok", **result}


@app.post("/api/process")
def api_process(
    body: MobileProcessRequest,
    _: None = Depends(verify_auth),
) -> dict:
    try:
        options = resolve_process_options(
            preset=body.preset,
            options=body.options,
            enhance_available=ENHANCE_AVAILABLE,
        )
        result = enqueue_process_filenames(body.filenames, options)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", **result}


@app.get("/api/jobs/ready")
def api_jobs_ready(_: None = Depends(verify_auth)) -> dict:
    return {"status": "ok", "jobs": mobile_list_ready_jobs()}


@app.get("/api/jobs/{job_id}/download")
def api_job_download(
    job_id: int,
    background_tasks: BackgroundTasks,
    delete_after: bool = False,
    _: None = Depends(verify_auth),
) -> FileResponse:
    try:
        job, payload = get_job_download(job_id)
    except DownloadError as exc:
        msg = str(exc)
        if msg == "job not found":
            raise HTTPException(404, msg) from exc
        if msg == "job not ready":
            raise HTTPException(400, msg) from exc
        raise HTTPException(404, msg) from exc

    background_tasks.add_task(
        cleanup_after_download,
        payload,
        delete_artifacts=delete_after,
        delete_job_fn=delete_job,
        job_id=job_id,
    )
    return FileResponse(payload.path, filename=payload.filename)


@app.post("/jobs/{job_id}/cancel", response_class=HTMLResponse)
def cancel_job(
    request: Request,
    job_id: int,
    _: None = Depends(verify_auth),
) -> HTMLResponse:
    ok, msg = request_cancel(job_id)
    flash = msg if ok else f"Не удалось прервать: {msg}"
    return templates.TemplateResponse(
        "jobs_table.html",
        {"request": request, "jobs": list_jobs(), "flash": flash},
    )


@app.post("/input/delete-selected", response_class=HTMLResponse)
async def delete_selected_input_files(
    request: Request,
    filenames: list[str] = Form(default=[]),
    _: None = Depends(verify_auth),
) -> HTMLResponse:
    if not filenames:
        flash = "Выберите файлы для удаления"
    else:
        deleted = skipped = 0
        seen: set[str] = set()
        for raw in filenames:
            name = Path(raw).name
            if name in seen:
                continue
            seen.add(name)
            path = (INPUT_DIR / name).resolve()
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            if path.parent.resolve() != INPUT_DIR.resolve():
                continue
            if has_active_job_for_input(str(path)):
                skipped += 1
                continue
            path.unlink()
            deleted += 1

        if deleted and skipped:
            flash = f"Удалено из input/: {deleted}, пропущено (в очереди): {skipped}"
        elif deleted:
            flash = f"Удалено из input/: {deleted}"
        elif skipped:
            flash = "Выделенные файлы в очереди или обрабатываются — удаление отменено"
        else:
            flash = "Не удалось удалить выделенные файлы"

    return templates.TemplateResponse(
        "process_panel.html",
        _panel_ctx(request, flash=flash),
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    _: None = Depends(verify_auth),
) -> HTMLResponse:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved_audio = 0
    errors: list[str] = []
    cue_toast: dict | None = None

    for uf in files:
        if not uf.filename:
            continue
        name = Path(uf.filename).name
        suffix = Path(name).suffix.lower()
        dest = INPUT_DIR / name
        content = await uf.read()

        if suffix == CUE_EXTENSION:
            dest.write_bytes(content)
            ok, missing, sheet = validate_cue(dest, INPUT_DIR)
            if not ok:
                errors.append(f"CUE {name}: не найдены — {', '.join(missing)}")
            elif sheet is not None:
                cue_toast = cue_info_dict(sheet)
            continue

        if suffix not in AUDIO_EXTENSIONS:
            continue

        dest.write_bytes(content)
        saved_audio += 1

    flash_parts: list[str] = []
    if saved_audio:
        flash_parts.append(f"Аудио: {saved_audio}")
    if errors:
        flash_parts.extend(errors)

    return templates.TemplateResponse(
        "process_panel.html",
        _panel_ctx(
            request,
            flash="; ".join(flash_parts) if flash_parts else None,
            cue_toast=cue_toast,
        ),
    )


@app.post("/process", response_class=HTMLResponse)
async def process_files(
    request: Request,
    filenames: list[str] = Form(default=[]),
    denoise: str = Form(""),
    eq: str = Form(""),
    highpass_hz: str = Form("80"),
    lowpass_hz: str = Form("10000"),
    compand: str = Form(""),
    compand_intensity: str = Form("50"),
    loudnorm: str = Form(""),
    enhance: str = Form(""),
    enhance_lowpass: str = Form(""),
    resample_441: str = Form("on"),
    afftdn_slider: str = Form("50"),
    afftdn_nf_slider: str = Form("50"),
    output_format: str = Form("wav"),
    mp3_bitrate: str = Form("320"),
    _: None = Depends(verify_auth),
) -> HTMLResponse:
    options = _pipeline_form_dict(
        denoise=denoise, eq=eq, highpass_hz=highpass_hz, lowpass_hz=lowpass_hz,
        compand=compand, compand_intensity=compand_intensity, loudnorm=loudnorm,
        enhance=enhance, enhance_lowpass=enhance_lowpass, resample_441=resample_441,
        afftdn_slider=afftdn_slider, afftdn_nf_slider=afftdn_nf_slider,
        output_format=output_format, mp3_bitrate=mp3_bitrate,
    )

    if options.get("enhance") and not ENHANCE_AVAILABLE:
        raise HTTPException(400, "AI-улучшение недоступно")

    if not filenames:
        return templates.TemplateResponse(
            "jobs_table.html",
            {"request": request, "jobs": list_jobs(), "flash": "Выберите хотя бы один файл"},
        )

    queued = skipped_noop = 0
    for name in filenames:
        path = INPUT_DIR / Path(name).name
        if not path.is_file():
            continue
        if not has_transformation(parse_options(options), path.suffix):
            skipped_noop += 1
            continue
        if _enqueue_process(path, options) is not None:
            queued += 1

    msg = f"В очередь: {queued}"
    if skipped_noop:
        msg += f", без изменений: {skipped_noop}"
    return templates.TemplateResponse(
        "jobs_table.html",
        {"request": request, "jobs": list_jobs(), "flash": msg},
    )


@app.post("/process-cue", response_class=HTMLResponse)
async def process_cue(
    request: Request,
    cue_name: str = Form(...),
    cue_mode: str = Form(...),
    split_format: str = Form("wav"),
    denoise: str = Form(""),
    eq: str = Form(""),
    highpass_hz: str = Form("80"),
    lowpass_hz: str = Form("10000"),
    compand: str = Form(""),
    compand_intensity: str = Form("50"),
    loudnorm: str = Form(""),
    enhance: str = Form(""),
    enhance_lowpass: str = Form(""),
    resample_441: str = Form("on"),
    afftdn_slider: str = Form("50"),
    afftdn_nf_slider: str = Form("50"),
    output_format: str = Form("wav"),
    mp3_bitrate: str = Form("320"),
    _: None = Depends(verify_auth),
) -> HTMLResponse:
    cue_path = INPUT_DIR / Path(cue_name).name
    ok, missing, sheet = validate_cue(cue_path, INPUT_DIR)
    if not ok or sheet is None:
        return templates.TemplateResponse(
            "jobs_table.html",
            {
                "request": request,
                "jobs": list_jobs(),
                "flash": f"CUE: не найдены — {', '.join(missing)}",
            },
        )

    pipeline = _pipeline_form_dict(
        denoise=denoise, eq=eq, highpass_hz=highpass_hz, lowpass_hz=lowpass_hz,
        compand=compand, compand_intensity=compand_intensity, loudnorm=loudnorm,
        enhance=enhance, enhance_lowpass=enhance_lowpass, resample_441=resample_441,
        afftdn_slider=afftdn_slider, afftdn_nf_slider=afftdn_nf_slider,
        output_format=output_format, mp3_bitrate=mp3_bitrate,
    )

    if pipeline.get("enhance") and not ENHANCE_AVAILABLE:
        raise HTTPException(400, "AI-улучшение недоступно")

    queued = 0

    if sheet.is_multi_file:
        if cue_mode != "batch":
            return templates.TemplateResponse(
                "jobs_table.html",
                {
                    "request": request,
                    "jobs": list_jobs(),
                    "flash": "Несколько FILE в CUE — только пакетная обработка",
                },
            )
        if _enqueue_cue_batch(cue_path, pipeline) is not None:
            queued = 1
    elif cue_mode == "split":
        fmt = split_format if split_format in ("wav", "flac", "mp3") else "wav"
        if _enqueue_cue_split(cue_path, fmt) is not None:
            queued = 1
    elif cue_mode == "process_whole":
        audio = sheet.files[0].resolved
        if audio and _enqueue_process(audio, pipeline) is not None:
            queued = 1
    else:
        return templates.TemplateResponse(
            "jobs_table.html",
            {"request": request, "jobs": list_jobs(), "flash": "Неизвестный режим CUE"},
        )

    return templates.TemplateResponse(
        "jobs_table.html",
        {"request": request, "jobs": list_jobs(), "flash": f"В очередь: {queued}"},
    )


@app.post("/download/ready")
def download_ready(
    background_tasks: BackgroundTasks,
    delete_after: str = Form(""),
    _: None = Depends(verify_auth),
) -> FileResponse:
    jobs = list_jobs()
    try:
        batch = prepare_batch_download(
            jobs, input_dir=INPUT_DIR, output_dir=OUTPUT_DIR,
        )
    except DownloadError as exc:
        raise HTTPException(404, str(exc)) from exc

    do_delete = delete_after == "on"
    background_tasks.add_task(
        cleanup_after_batch_download,
        batch,
        delete_artifacts=do_delete,
        delete_job_fn=delete_job,
    )
    return FileResponse(batch.path, filename=batch.filename)


@app.post("/download/{job_id}")
def download(
    job_id: int,
    background_tasks: BackgroundTasks,
    delete_after: str = Form(""),
    _: None = Depends(verify_auth),
) -> FileResponse:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.get("status") != "done":
        raise HTTPException(400, "Задача ещё не завершена")

    try:
        payload = prepare_download(job, input_dir=INPUT_DIR, output_dir=OUTPUT_DIR)
    except DownloadError as exc:
        raise HTTPException(404, str(exc)) from exc

    do_delete = delete_after == "on"
    background_tasks.add_task(
        cleanup_after_download,
        payload,
        delete_artifacts=do_delete,
        delete_job_fn=delete_job,
        job_id=job_id,
    )
    return FileResponse(payload.path, filename=payload.filename)


static_dir = APP_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
