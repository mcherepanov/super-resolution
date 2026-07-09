FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN ffmpeg -version >/dev/null

RUN pip install --no-cache-dir pika soundfile

WORKDIR /app

COPY scripts/worker.py scripts/db.py scripts/ffmpeg_ops.py \
    scripts/audio_pipeline.py scripts/process_options.py \
    scripts/cue_sheet.py scripts/cue_split.py scripts/download_utils.py /app/scripts/

ENV MOCK_MODE=1
ENV PYTHONPATH=/app/scripts

CMD ["python3", "scripts/worker.py"]
