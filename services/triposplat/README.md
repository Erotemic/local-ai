# TripoSplat service

Local TripoSplat Gradio service for reconstructing 3D Gaussians from a single
input image.

Upstream project:

```text
https://github.com/VAST-AI-Research/TripoSplat
```

The upstream demo supports variable Gaussian counts up to 262,144 and exports
`.ply` or `.splat` files.

## Storage

Defaults:

```text
/data/hf-repos/VAST-AI/TripoSplat/   canonical checkpoint tree, mounted read-only
/data/service/triposplat/outputs/    generated outputs
```

Weights are deliberately not baked into the Docker image.

## Setup

```bash
cp .env.template .env
./scripts/download_weights.sh
./setup.sh
```

The UI is available at `http://127.0.0.1:7861` by default. Port 7861 avoids the
ACE-Step default on 7860.

## GPU

The default is host GPU 0. Set `TRIPOSPLAT_GPU` in `.env` to select another
NVIDIA device.

## Reproducibility

`TRIPOSPLAT_REF` defaults to `main` while this service is exploratory. Pin it to
a known commit when a working configuration becomes worth preserving exactly.
The PyTorch CUDA wheel index is also configurable through `TORCH_INDEX_URL`.

## Diagnostics

```bash
./scripts/doctor.sh
```
