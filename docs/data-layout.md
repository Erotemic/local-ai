# Data layout

The Git checkout describes services. Large, generated, cached, and mutable data
stays outside it.

## Machine roots

The root `.env` defines three machine-wide locations:

```text
HF_REPOS_ROOT=/data/hf-repos
LOCAL_AI_SERVICE_ROOT=/data/local-ai/services
LOCAL_AI_WORKSPACES_ROOT=/data/local-ai/workspaces
```

Every `./setup.sh` prints the resolved paths before creating directories or
downloading model data.

## Canonical model repositories

Default:

```text
/data/hf-repos/
```

Use this for explicitly provisioned Hugging Face repositories that are useful
outside one application's private cache. Services normally mount these trees
read-only.

Current declared repositories:

```text
/data/hf-repos/
├── Comfy-Org/
│   └── MiniMax-H3/
│       ├── diffusion_models/
│       ├── text_encoders/
│       └── vae/
└── VAST-AI/
    └── TripoSplat/
        ├── diffusion_models/
        ├── vae/
        ├── clip_vision/
        └── background_removal/
```

These weights are reconstructible from their upstream repositories. Back them up
only if avoiding a future redownload is valuable.

## Service state

Default:

```text
/data/local-ai/services/
```

Concrete layout:

```text
/data/local-ai/services/
├── comfyui/
│   ├── models/              # ComfyUI-managed/local model files
│   ├── input/               # user inputs
│   ├── output/              # generated media
│   ├── user/                # user config/workflows; worth backing up
│   ├── custom_nodes/        # mutable node checkouts
│   └── cache/
│       ├── huggingface/     # disposable
│       └── torch/           # disposable
│
├── ace-step/
│   ├── models/              # explicit ACE-Step checkpoints; reconstructible
│   ├── outputs/             # generated audio
│   ├── huggingface/         # disposable cache
│   ├── torch/               # disposable cache
│   ├── uv-cache/            # disposable cache
│   └── cache/               # disposable application cache
│
└── triposplat/
    └── outputs/             # generated .ply/.splat assets
```

ACE-Step previously lived at `/data/service/docker/ace-step`. To reuse that
existing installation rather than redownload its model tree, set this in
`services/ace-step/.env` during setup:

```dotenv
ACESTEP_DATA_ROOT=/data/service/docker/ace-step
```

Other services can likewise override their `*_DATA_ROOT`, but leaving it blank
uses the machine-wide `LOCAL_AI_SERVICE_ROOT` convention.

## Workspaces

Default:

```text
/data/local-ai/workspaces/<project>/
```

A workspace contains data intentionally exchanged between services: source
images, generated media selected for another stage, exported `.ply` files, and
similar project artifacts.

Do not expose every workspace to every container. Mount a selected workspace
only for a workflow that needs it, for example:

```yaml
volumes:
  - ${LOCAL_AI_WORKSPACE}:/workspace:rw
```

Service-private state is not an interchange API. Copy/promote data into a
workspace rather than making another service depend on a private state tree.

## Secrets

Keep tokens in untracked `.env` files or another host secret store. The setup
helper writes `.env` files with mode `0600`.

A shell-provided `HF_TOKEN` or a value explicitly added to root `.env` is passed
to host-side Hugging Face downloads. Services do not receive that token unless
their Compose configuration explicitly requests it.
