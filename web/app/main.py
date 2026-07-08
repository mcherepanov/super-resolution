"""Web UI: upload, очередь, история."""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

import pika
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from db import (  # noqa: E402
    create_job,
    get_job,
    has_active_job,
    init_db,
    list_jobs,
    update_job,
)

APP_DIR = Path(__file__).resolve().parent
INPUT_DIR = Path(os.environ.get("INPUT_DIR", "/app/input"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/output"))
QUEUE_NAME = os.environ.get("QUEUE_NAME", "sr_jobs")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
MOCK_MODE = os.environ.get("MOCK_MODE", "").lower() in ("1", "true", "yes")

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".opus"}

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


def _output_path_for(input_path: Path) -> Path:
    rel = input_path.name
    stem = Path(rel).stem
    return OUTPUT_DIR / f"{stem}.wav"


def _enqueue_file(input_path: Path) -> int | None:
    """Создать job и отправить в очередь. None если пропуск."""
    input_path = input_path.resolve()
    output_path = _output_path_for(input_path)
    str_in = str(input_path)
    str_out = str(output_path)

    if output_path.exists():
        if not has_active_job(str_in):
            job_id = create_job(input_path.name, str_in, str_out)
            update_job(job_id, status="skipped", finished_at=_now_iso())
        return None

    if has_active_job(str_in):
        return None

    job_id = create_job(input_path.name, str_in, str_out)
    _rabbit_publish(job_id)
    return job_id


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@app.on_event("startup")
def startup() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: None = Depends(verify_auth)) -> HTMLResponse:
    jobs = list_jobs()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "jobs": jobs, "mock_mode": MOCK_MODE},
    )


@app.get("/jobs/partial", response_class=HTMLResponse)
def jobs_partial(request: Request, _: None = Depends(verify_auth)) -> HTMLResponse:
    jobs = list_jobs()
    return templates.TemplateResponse(
        "jobs_table.html",
        {"request": request, "jobs": jobs},
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    _: None = Depends(verify_auth),
) -> HTMLResponse:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    queued = 0
    for uf in files:
        if not uf.filename:
            continue
        suffix = Path(uf.filename).suffix.lower()
        if suffix not in AUDIO_EXTENSIONS:
            continue
        dest = INPUT_DIR / Path(uf.filename).name
        content = await uf.read()
        dest.write_bytes(content)
        if _enqueue_file(dest) is not None:
            queued += 1
    jobs = list_jobs()
    return templates.TemplateResponse(
        "jobs_table.html",
        {
            "request": request,
            "jobs": jobs,
            "flash": f"Загружено, в очередь: {queued}",
        },
    )


@app.post("/queue-scan", response_class=HTMLResponse)
def queue_scan(request: Request, _: None = Depends(verify_auth)) -> HTMLResponse:
    queued = 0
    if INPUT_DIR.exists():
        for path in sorted(INPUT_DIR.iterdir()):
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                if _enqueue_file(path) is not None:
                    queued += 1
    jobs = list_jobs()
    return templates.TemplateResponse(
        "jobs_table.html",
        {
            "request": request,
            "jobs": jobs,
            "flash": f"Добавлено в очередь: {queued}",
        },
    )


@app.get("/download/{job_id}")
def download(job_id: int, _: None = Depends(verify_auth)) -> FileResponse:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    path = Path(job["output_path"])
    if not path.is_file():
        raise HTTPException(404, "Output file not found")
    return FileResponse(path, filename=job["filename"].rsplit(".", 1)[0] + ".wav")


# статика (минимальный css)
static_dir = APP_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
