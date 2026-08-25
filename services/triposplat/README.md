# TripoSplat service

Local TripoSplat Gradio service for reconstructing 3D Gaussians from a single
input image.

## First use

```bash
./setup.sh
./start.sh
```

Do not download weights first. Setup initializes configuration, shows the exact
checkpoint destination, creates the directories, builds the image, downloads the
required TripoSplat repository, verifies it, and then exits.

Default resolved storage:

```text
/data/hf-repos/VAST-AI/TripoSplat/       required canonical weights, read-only in container
/data/local-ai/services/triposplat/      private service state
└── outputs/                             generated .ply/.splat assets
```

The UI defaults to `http://127.0.0.1:7861`.

## Configuration

`./setup.sh` creates/edits both the root machine configuration and this service's
`.env` when needed. Reopen them later with:

```bash
./setup.sh --edit
```

Service-specific choices include `TRIPOSPLAT_GPU`, `TRIPOSPLAT_PORT`, upstream
revision/image settings, and an optional `TRIPOSPLAT_DATA_ROOT` override. Leave
the data-root override blank to inherit `/data/local-ai/services/triposplat`.

## Weights

The required bundle is declared in `service.toml`:

```text
source:       hf://VAST-AI/TripoSplat
destination: /data/hf-repos/VAST-AI/TripoSplat
container:   /opt/TripoSplat/ckpts (read-only)
```

Normal setup provisions it automatically. The low-level maintenance commands
remain available after configuration exists:

```bash
./scripts/check_weights.sh
./scripts/download_weights.sh
```

Downloads use `uvx --from huggingface_hub hf`, so a global `hf` command is not
required.

## Start contract

`./start.sh` does not create `.env`, download weights, or build the image. It
checks that required model data and directories are present and starts with
`docker compose up --no-build`.

## Reproducibility

`TRIPOSPLAT_REF` defaults to `main` while this service is exploratory. Pin it to
a known commit when a working configuration becomes worth preserving exactly.
The PyTorch CUDA wheel index is configurable through `TORCH_INDEX_URL`.
