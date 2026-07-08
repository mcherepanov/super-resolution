FROM nvidia/cuda:12.2.0-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    ffmpeg \
    git \
    mc \
    nano \
    curl \
    wget \
    nvtop \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 config set global.timeout 600
RUN pip3 install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

RUN pip3 install --no-cache-dir \
    einops \
    librosa \
    soundfile \
    tqdm \
    scipy \
    huggingface-hub \
    matplotlib \
    pika

WORKDIR /app

COPY vendor/FlashSR /app/vendor/FlashSR
RUN pip3 install --no-cache-dir -e /app/vendor/FlashSR

COPY scripts /app/scripts

CMD ["/bin/bash"]
