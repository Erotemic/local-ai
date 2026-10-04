FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    espeak-ng \
    ffmpeg \
    gcc \
    libc6-dev \
    libsndfile1 \
    libsox-fmt-all \
    sox \
    && rm -rf /var/lib/apt/lists/*

# PyTorch 2.14 / CUDA 12.6 is deliberately pinned for Pascal support. CUDA 13
# wheels no longer contain pre-Turing kernels.
ARG TORCH_VERSION=2.14.0
ARG TORCHAUDIO_VERSION=2.11.0
ARG TORCH_CUDA_TAG=cu126
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install \
        "torch==${TORCH_VERSION}+${TORCH_CUDA_TAG}" \
        "torchaudio==${TORCHAUDIO_VERSION}+${TORCH_CUDA_TAG}" \
        --index-url "https://download.pytorch.org/whl/${TORCH_CUDA_TAG}"

# Pin the Qwen engine independently from Wavhost's permissive lower bound so a
# local-ai rebuild stays an experiment we can reproduce.
ARG QWEN_TTS_VERSION=0.1.1
RUN python -m pip install "qwen-tts==${QWEN_TTS_VERSION}"

# Build from the checked-out submodule, including local/upstreamable fixes.
COPY submodules/wavhost /opt/wavhost
RUN python -m pip install "/opt/wavhost[qwen,kokoro]" \
    && python -m pip check

WORKDIR /opt/wavhost
EXPOSE 11435
CMD ["wavhost", "serve", "--host", "0.0.0.0", "--port", "11435"]
