"""RabbitMQ: параметры подключения и публикация задач."""

from __future__ import annotations

import json
import os

import pika

QUEUE_NAME = os.environ.get("QUEUE_NAME", "sr_jobs")


def rabbit_connection_params(*, heartbeat: int | None = 0) -> pika.ConnectionParameters:
    host = os.environ.get("RABBITMQ_HOST", "rabbitmq")
    port = int(os.environ.get("RABBITMQ_PORT", "5672"))
    user = os.environ.get("RABBITMQ_USER", "guest")
    password = os.environ.get("RABBITMQ_PASSWORD", "guest")
    kwargs: dict = {
        "host": host,
        "port": port,
        "credentials": pika.PlainCredentials(user, password),
        "blocked_connection_timeout": 300,
    }
    if heartbeat is not None:
        kwargs["heartbeat"] = heartbeat
    return pika.ConnectionParameters(**kwargs)


def publish_job(job_id: int, *, queue_name: str = QUEUE_NAME) -> None:
    connection = pika.BlockingConnection(rabbit_connection_params(heartbeat=None))
    try:
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=json.dumps({"job_id": job_id}),
            properties=pika.BasicProperties(delivery_mode=2),
        )
    finally:
        connection.close()
