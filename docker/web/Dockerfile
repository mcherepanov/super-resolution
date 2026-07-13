FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY web/app /app/app
COPY scripts/db.py scripts/messaging.py scripts/mobile_status.py scripts/process_options.py scripts/ffmpeg_ops.py \
    scripts/cue_sheet.py scripts/download_utils.py /app/scripts/

ENV PYTHONPATH=/app/scripts:/app

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
