#!/usr/bin/env python3
"""Worker: RabbitMQ → ffmpeg / FlashSR / CUE."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pika

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audio_pipeline import run_pipeline
from cue_split import run_cue_batch, run_cue_split
from db import get_job, init_db, update_job
from ffmpeg_ops import FfmpegError

WEIGHTS_DIR = os.environ.get("WEIGHTS_DIR", "/app/weights")
QUEUE_NAME = os.environ.get("QUEUE_NAME", "sr_jobs")
MOCK_MODE = os.environ.get("MOCK_MODE", "").lower() in ("1", "true", "yes")


def _rabbit_params() -> pika.ConnectionParameters:
    host = os.environ.get("RABBITMQ_HOST", "rabbitmq")
    port = int(os.environ.get("RABBITMQ_PORT", "5672"))
    user = os.environ.get("RABBITMQ_USER", "guest")
    password = os.environ.get("RABBITMQ_PASSWORD", "guest")
    return pika.ConnectionParameters(
        host=host,
        port=port,
        credentials=pika.PlainCredentials(user, password),
        heartbeat=600,
        blocked_connection_timeout=300,
    )


def _cleanup_work_files(dst: Path, job_id: int) -> None:
    for p in dst.parent.glob(f".work_{dst.stem}_{job_id}*.wav"):
        p.unlink(missing_ok=True)
    legacy = dst.with_name(dst.stem + "_48k.wav")
    if legacy.exists() and not dst.exists():
        legacy.unlink()


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _job_options(job: dict) -> dict:
    raw = job.get("options")
    if not raw:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def _process_pipeline_job(job_id: int, job: dict, model: Any, device: Any) -> None:
    src = Path(job["input_path"])
    dst = Path(job["output_path"])

    if not src.exists():
        update_job(
            job_id,
            status="failed",
            finished_at=_iso_now(),
            error_message=f"input not found: {src}",
        )
        return

    _cleanup_work_files(dst, job_id)
    tag = "[MOCK] " if MOCK_MODE else ""
    print(f"Job {job_id}: {tag}processing {src.name}")

    dur, orig_sr, out_sr = run_pipeline(
        job_id,
        src,
        dst,
        job.get("options"),
        model=model,
        device=device,
    )
    update_job(
        job_id,
        status="done",
        finished_at=_iso_now(),
        duration_sec=dur,
        input_sr=orig_sr,
        output_sr=out_sr,
    )
    print(f"Job {job_id}: done ({dur:.1f}s audio)")


def _process_cue_split_job(job_id: int, job: dict) -> None:
    cue_path = Path(job["input_path"])
    opts = _job_options(job)
    split_format = opts.get("split_format", "wav")
    audio_path = opts.get("audio_path")
    audio = Path(audio_path) if audio_path else None

    if not cue_path.is_file():
        raise FileNotFoundError(f"cue not found: {cue_path}")

    count, dur, out_dir = run_cue_split(
        cue_path,
        audio_path=audio,
        split_format=split_format,
    )
    update_job(
        job_id,
        status="done",
        finished_at=_iso_now(),
        duration_sec=dur,
        output_path=str(out_dir),
        error_message=None,
    )
    print(f"Job {job_id}: cue split → {count} tracks in {out_dir}")


def _process_cue_batch_job(job_id: int, job: dict, model: Any, device: Any) -> None:
    cue_path = Path(job["input_path"])
    opts = _job_options(job)
    pipeline_opts = opts.get("pipeline", {})
    output_files: list[str] = []

    def _run_one(audio: Path) -> None:
        out_fmt = pipeline_opts.get("output_format", "wav")
        out_path = Path(job["output_path"]).parent / f"{audio.stem}.{out_fmt}"
        run_pipeline(
            job_id,
            audio,
            out_path,
            pipeline_opts,
            model=model,
            device=device,
        )
        output_files.append(str(out_path))

    ok, total, errors = run_cue_batch(cue_path, _run_one)
    opts["output_files"] = output_files
    msg = f"batch {ok}/{total}"
    if errors:
        msg += ": " + "; ".join(errors[:5])
    status = "done" if ok == total else ("failed" if ok == 0 else "done")
    update_job(
        job_id,
        status=status,
        finished_at=_iso_now(),
        error_message=msg if errors else None,
        options=json.dumps(opts, ensure_ascii=False),
    )
    print(f"Job {job_id}: {msg}")


def process_job(job_id: int, model: Any, device: Any) -> None:
    job = get_job(job_id)
    if job is None:
        print(f"Job {job_id}: not found in DB, skip")
        return

    job_type = job.get("job_type") or "process"
    update_job(job_id, status="processing", started_at=_iso_now())

    try:
        if job_type == "cue_split":
            _process_cue_split_job(job_id, job)
        elif job_type == "cue_batch":
            _process_cue_batch_job(job_id, job, model, device)
        else:
            _process_pipeline_job(job_id, job, model, device)
    except FfmpegError as exc:
        dst = Path(job.get("output_path", ""))
        if dst.name:
            _cleanup_work_files(dst, job_id)
        update_job(
            job_id,
            status="failed",
            finished_at=_iso_now(),
            error_message=str(exc)[:2000],
        )
        print(f"Job {job_id}: ffmpeg failed — {exc}")
    except Exception as exc:
        dst = Path(job.get("output_path", ""))
        if dst.name:
            _cleanup_work_files(dst, job_id)
        update_job(
            job_id,
            status="failed",
            finished_at=_iso_now(),
            error_message=str(exc)[:2000],
        )
        print(f"Job {job_id}: failed — {exc}")


def _load_model() -> tuple[Any, Any]:
    import torch
    from super_resolve import build_model

    dev = torch.device(
        os.environ.get("DEVICE", "cuda") if torch.cuda.is_available() else "cpu"
    )
    print(f"Worker device: {dev}")
    print("Loading model...")
    t0 = time.monotonic()
    model = build_model(WEIGHTS_DIR, dev)
    print(f"Model loaded in {time.monotonic() - t0:.1f}s")
    return model, dev


def main() -> None:
    init_db()

    model, dev = None, None
    if MOCK_MODE:
        if not shutil.which("ffmpeg"):
            sys.exit("MOCK_MODE: ffmpeg не найден в PATH")
        print("*** Режим «Только обработка»: FlashSR отключён ***")
    else:
        model, dev = _load_model()

    while True:
        try:
            connection = pika.BlockingConnection(_rabbit_params())
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_qos(prefetch_count=1)

            def on_message(ch, method, _props, body: bytes) -> None:
                try:
                    payload = json.loads(body)
                    job_id = int(payload["job_id"])
                    process_job(job_id, model, dev)
                except Exception as exc:
                    print(f"Message error: {exc}")
                finally:
                    ch.basic_ack(delivery_tag=method.delivery_tag)

            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
            print(f"Waiting for messages on queue '{QUEUE_NAME}'...")
            channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as exc:
            print(f"RabbitMQ not ready ({exc}), retry in 5s...")
            time.sleep(5)


if __name__ == "__main__":
    main()
