# syntax=docker/dockerfile:1
ARG BASE_COMFYUI_IMAGE=ghcr.io/lecode-official/comfyui-docker:latest
FROM ${BASE_COMFYUI_IMAGE}

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG COMFYUI_REF=v0.31.0

RUN set -eux; \
    cd /opt/comfyui; \
    git remote set-url origin https://github.com/Comfy-Org/ComfyUI.git; \
    git fetch --tags --force origin; \
    git checkout --force "${COMFYUI_REF}"; \
    /opt/conda/bin/python -m pip install --upgrade pip; \
    /opt/conda/bin/python -m pip install --requirement requirements.txt

WORKDIR /opt/comfyui
