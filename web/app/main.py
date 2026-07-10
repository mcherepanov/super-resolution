"""Web UI: upload, очередь, история, CUE."""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

import pika
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cue_sheet import (  # noqa: E402
    cue_info_dict,
    parse_cue,
    split_output_dir,
    validate_cue,
)
from db import (  # noqa: E402
    create_job,
    delete_job,
    get_job,
    has_active_job,
    init_db,
    list_jobs,
)
from job_cancel import request_cancel  # noqa: E402
from download_utils import (  # noqa: E402
    DownloadError,
    cleanup_after_download,
    prepare_download,
)
from ffmpeg_ops import INPUT_EXTENSIONS  # noqa: E402
from process_options import (  # noqa: E402
    has_transformation,
    options_from_form,
    options_summary,
    parse_options,
)

APP_DIR = Path(__file__).resolve().parent
INPUT_DIR = Path(os.environ.get("INPUT_DIR", "/app/input"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/output"))
QUEUE_NAME = os.environ.get("QUEUE_NAME", "sr_jobs")
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
    host = os.environ.get("RABBITMQ_HOST", "rabbitmq")
    port = int(os.environ.get("RABBITMQ_PORT", "5672"))
    user = os.environ.get("RABBITMQ_USER", "guest")
    password = os.environ.get("RABBITMQ_PASSWORD", "guest")
    params = pika.ConnectionParameters(
        host=host,
        port=port,
        credentials=pika.PlainCredentials(user, password),
    )
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=QUEUE_NAME,
        body=json.dumps({"job_id": job_id}),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    connection.close()


def _output_path_for(input_path: Path, output_format: str) -> Path:
    stem = input_path.stem
    ext = output_format if output_format.startswith(".") else f".{output_format}"
    return OUTPUT_DIR / f"{stem}{ext}"


def _list_input_files() -> list[Path]:
    if not INPUT_DIR.exists():
        return []
    return sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )


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
        options={"pipeline": pipeline, "cue_path": str_cue},
        output_format=pipeline.get("output_format", "wav"),
        job_type="cue_batch",
    )
    _rabbit_publish(job_id)
    return job_id


def _job_options_summary(job: dict) -> str:
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


@app.on_event("startup")
def startup() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    init_db()


templates.env.globals["job_options_summary"] = _job_options_summary
templates.env.globals["enhance_available"] = ENHANCE_AVAILABLE


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
    _: None = Depends(verify_auth),
) -> HTMLResponse:
    options = _pipeline_form_dict(
        denoise=denoise, eq=eq, highpass_hz=highpass_hz, lowpass_hz=lowpass_hz,
        compand=compand, compand_intensity=compand_intensity, loudnorm=loudnorm,
        enhance=enhance, enhance_lowpass=enhance_lowpass, resample_441=resample_441,
        afftdn_slider=afftdn_slider, afftdn_nf_slider=afftdn_nf_slider,
        output_format=output_format,
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
        output_format=output_format,
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
