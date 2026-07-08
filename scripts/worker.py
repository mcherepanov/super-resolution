#!/usr/bin/env python3
"""Worker: RabbitMQ → FlashSR (или mock без GPU)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pika

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_job, init_db, update_job

WEIGHTS_DIR = os.environ.get("WEIGHTS_DIR", "/app/weights")
QUEUE_NAME = os.environ.get("QUEUE_NAME", "sr_jobs")
LOWPASS = os.environ.get("LOWPASS", "").lower() in ("1", "true", "yes")
MOCK_MODE = os.environ.get("MOCK_MODE", "").lower() in ("1", "true", "yes")
MOCK_DELAY_SEC = float(os.environ.get("MOCK_DELAY_SEC", "3"))
OUTPUT_SR = 44_100
TARGET_SR = 48_000


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


def _cleanup_incomplete(dst: Path) -> None:
    temp = dst.with_name(dst.stem + "_48k.wav")
    if temp.exists() and not dst.exists():
        temp.unlink()
    if dst.exists() and temp.exists():
        dst.unlink()
        temp.unlink()


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def mock_enhance_file(src: Path, dst: Path) -> tuple[float, int, int, float]:
    """Имитация обработки: пауза + passthrough через ffmpeg (без FlashSR)."""
    import soundfile as sf

    data, sr = sf.read(str(src), dtype="float32")
    duration = len(data) / sr if sr > 0 else 1.0
    delay = min(max(duration * 0.05, MOCK_DELAY_SEC * 0.5), MOCK_DELAY_SEC * 3)
    print(f"  [MOCK] sleep {delay:.1f}s ...")
    time.sleep(delay)

    dst.parent.mkdir(parents=True, exist_ok=True)
    temp = dst.with_name(dst.stem + "_48k.wav")
    sf.write(str(temp), data, TARGET_SR)
    subprocess.run(
        ["ffmpeg", "-i", str(temp), "-ar", str(OUTPUT_SR), str(dst), "-y"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    temp.unlink()
    speed = duration / delay if delay > 0 else 0
    return duration, sr, OUTPUT_SR, speed


def process_job(job_id: int, model: Any, device: Any) -> None:
    job = get_job(job_id)
    if job is None:
        print(f"Job {job_id}: not found in DB, skip")
        return

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

    _cleanup_incomplete(dst)

    if dst.exists() and not dst.with_name(dst.stem + "_48k.wav").exists():
        update_job(job_id, status="skipped", finished_at=_iso_now())
        print(f"Job {job_id}: skipped (output exists)")
        return

    update_job(job_id, status="processing", started_at=_iso_now())
    tag = "[MOCK] " if MOCK_MODE else ""
    print(f"Job {job_id}: {tag}processing {src.name}")

    try:
        if MOCK_MODE:
            dur, orig_sr, out_sr, _speed = mock_enhance_file(src, dst)
        else:
            from super_resolve import enhance_file
            dur, orig_sr, out_sr, _speed = enhance_file(
                model, src, dst, device=device, lowpass=LOWPASS
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
    except Exception as exc:
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
        print("*** MOCK_MODE: FlashSR отключён, имитация обработки ***")
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
