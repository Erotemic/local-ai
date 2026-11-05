# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.8.15 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential portaudio19-dev libsndfile1 ffmpeg && rm -rf /var/lib/apt/lists/*
ARG FISH_SOURCE_REVISION=0f8a12e8a651062fdee9c33a894bdccd3a8513f1
RUN git clone https://github.com/fishaudio/fish-speech.git /opt/fish-speech && git -C /opt/fish-speech checkout "$FISH_SOURCE_REVISION"
WORKDIR /opt/fish-speech
RUN uv sync --extra cu126 --frozen
ENV PATH="/opt/fish-speech/.venv/bin:${PATH}" PYTHONPATH=/opt/fish-speech PYTHONUNBUFFERED=1
COPY services/tts/scripts/candidate_server.py /opt/local-ai/candidate_server.py
ENTRYPOINT ["python", "/opt/local-ai/candidate_server.py"]
