FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pika soundfile

WORKDIR /app

COPY scripts/worker.py scripts/db.py /app/scripts/

ENV MOCK_MODE=1
ENV PYTHONPATH=/app/scripts

CMD ["python3", "scripts/worker.py"]
