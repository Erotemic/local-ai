FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir torch torchvision --index-url "${TORCH_INDEX_URL}" \
    && python -m pip install --no-cache-dir numpy safetensors pillow tqdm gradio

ARG TRIPOSPLAT_REPO=https://github.com/VAST-AI-Research/TripoSplat.git
ARG TRIPOSPLAT_REF=main

WORKDIR /opt
RUN git clone "${TRIPOSPLAT_REPO}" TripoSplat \
    && cd TripoSplat \
    && git checkout "${TRIPOSPLAT_REF}"

WORKDIR /opt/TripoSplat
EXPOSE 7860
CMD ["python", "run_gradio.py"]
