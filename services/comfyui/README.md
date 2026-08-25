# ComfyUI service

General local ComfyUI service. The service is not tied to one model family.
MiniMax H3 support is retained from the setup that originally motivated this
container, but additional ComfyUI models and workflows can use the same service.

## Storage

Defaults:

```text
/data/hf-repos/          canonical shared model repositories, mounted read-only
/data/service/comfyui/   mutable ComfyUI state
```

ComfyUI's mutable tree contains its own models, inputs, outputs, user data,
custom nodes, and caches. Canonical Hugging Face repositories are mounted at
`/models/hf` and can be exposed to ComfyUI through `extra_model_paths.yaml`.

## Setup

```bash
cp .env.template .env
./setup.sh
```

The UI is available at `http://127.0.0.1:8188` by default.

Inspect GPU topology separately if useful:

```bash
./scripts/gpu_topology.sh
```

`PRIMARY_GPU=auto` selects the GPU with the widest active PCIe link. Set a GPU
index explicitly if that heuristic is not appropriate on the current host.

## MiniMax H3

MiniMax H3 is one optional model family supported by this ComfyUI service. The
existing native-ComfyUI helper scripts are retained:

```bash
./scripts/download_minimax_h3_comfy.sh
./scripts/check_minimax_h3_models.sh
```

They use the canonical repository path:

```text
/data/hf-repos/Comfy-Org/MiniMax-H3/
```

The `extra_model_paths.yaml` mapping makes those weights available to native
ComfyUI H3 workflows without copying them into ComfyUI's private model tree.

## Diagnostics

```bash
./scripts/doctor.sh
```

## Network exposure

The host bind address defaults to `127.0.0.1`. To expose ComfyUI beyond the
local machine, set `LOCAL_AI_BIND_ADDRESS` explicitly in `.env` and consider the
security implications of installed custom nodes before doing so.

The previous setup used host IPC and an unconfined seccomp profile. Those are no
longer defaults. If a specific workload proves to require either setting, add it
locally with a Compose override and document why.
