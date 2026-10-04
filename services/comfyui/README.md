# ComfyUI service

General local ComfyUI service. The service is not tied to one model family.
MiniMax H3 is retained as an optional declared model bundle rather than part of
the service identity.

## First use

```bash
./setup.sh
./start.sh
```

Setup initializes configuration, prints the storage plan, creates directories,
and builds the image. ComfyUI itself has no required weight bundle.

Default resolved state:

```text
/data/services/local-ai/comfyui/
├── models/                 ComfyUI-managed/local models
├── input/                  input files
├── output/                 generated media
├── user/                   user config/workflows
├── custom_nodes/           mutable node checkouts
└── cache/
    ├── huggingface/        disposable cache
    └── torch/              disposable cache

/data/services/hf-repos/             canonical shared model repositories, mounted RO
```

The UI defaults to `http://127.0.0.1:8188`.

## Optional model bundles

Available now:

```text
minimax-h3
```

Select it while editing `.env`:

```dotenv
COMFYUI_MODEL_BUNDLES=minimax-h3
```

Then ordinary setup provisions the selected files into:

```text
/data/services/hf-repos/Comfy-Org/MiniMax-H3/
```

You can also request it for one setup run:

```bash
./setup.sh --with-model minimax-h3
```

The existing mapping in `extra_model_paths.yaml` exposes that canonical tree to
native ComfyUI model loaders without copying it into private ComfyUI state.
Model-specific notes live in `docs/minimax-h3.md`.

Low-level maintenance commands, after setup has initialized configuration:

```bash
./scripts/check_minimax_h3_models.sh
./scripts/download_minimax_h3_comfy.sh
```

## GPU selection

`PRIMARY_GPU=auto` selects the NVIDIA GPU with the widest active PCIe link. Set
an explicit GPU index in `.env` if desired.

Inspect topology with:

```bash
./scripts/gpu_topology.sh
```

## Start contract

`./start.sh` does not create configuration, download model bundles, or build the
image. It starts already-provisioned state with `docker compose up --no-build`.

## Network exposure

The root `LOCAL_AI_BIND_ADDRESS` defaults to `127.0.0.1`. Broader exposure is
explicit. The previous setup's host IPC and unconfined seccomp settings are not
defaults here.
