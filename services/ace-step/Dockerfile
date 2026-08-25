FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
    libsndfile1 \
    libgl1 \
    libglib2.0-0 \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

ARG ACESTEP_REPO=https://github.com/ACE-Step/ACE-Step-1.5.git
ARG ACESTEP_REF=main

WORKDIR /opt
RUN git clone "${ACESTEP_REPO}" ACE-Step-1.5 \
    && cd ACE-Step-1.5 \
    && git checkout "${ACESTEP_REF}"

WORKDIR /opt/ACE-Step-1.5
RUN uv sync

# Keep TorchCodec aligned with the PyTorch version that ACE-Step resolves.
RUN uv pip install --python .venv/bin/python --force-reinstall --no-deps \
    "torchcodec==0.10.*" \
    && uv pip install --python .venv/bin/python \
    pytorch_wavelets \
    PyWavelets

# Preserve the local preference for lossless FLAC as the UI default.
RUN python - <<'PY'
from pathlib import Path

root = Path('/opt/ACE-Step-1.5')
hits = []
for path in root.rglob('*.py'):
    text = path.read_text()
    new = text
    replacements = [
        ('value="mp3"', 'value="flac"'),
        ("value='mp3'", "value='flac'"),
        ('"audio_format": "mp3"', '"audio_format": "flac"'),
        ("'audio_format': 'mp3'", "'audio_format': 'flac'"),
        ('default="mp3"', 'default="flac"'),
        ("default='mp3'", "default='flac'"),
    ]
    for old, replacement in replacements:
        new = new.replace(old, replacement)
    if new != text:
        path.write_text(new)
        hits.append(str(path.relative_to(root)))
print('Patched ACE-Step audio defaults:', hits)
PY

EXPOSE 7860 8001
CMD ["uv", "run", "--no-sync", "acestep", "--server-name", "0.0.0.0", "--port", "7860"]
