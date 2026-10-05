# local-ai

Version-controlled recipes for local generative-AI services.

This repository is a collection of independently runnable local services, not an
inference orchestration framework. Each service owns its Docker image, Compose
configuration, model requirements, state, and lifecycle. A small shared Python
helper makes first-run configuration and persistent storage explicit.

## Services

| Service | Purpose | Default UI/API |
| --- | --- | --- |
| `comfyui` | General ComfyUI installation; model bundles are optional | `http://127.0.0.1:8188` |
| `ace-step` | ACE-Step music/audio generation | `http://127.0.0.1:7860` |
| `triposplat` | Single-image 3D Gaussian reconstruction | `http://127.0.0.1:7861` |
| `tts` | Local TTS: quantized Qwen plus GPU/CPU Kokoro choices | `http://127.0.0.1:11437` |

`infer-stack` is intentionally not included yet. It can become another service
later without changing this repository's role.

The `tts` service deliberately talks to the selected inference runtimes
directly. qwentts.cpp provides the fast quantized Qwen path, while pinned
Kokoro-FastAPI GPU and CPU images provide simple alternative backends. The
qwentts client gateway is a small local Python + ffmpeg image used only for MP3
transport, plausibility checks, and retries.

## Normal workflow

The normal contract is the same for every service:

```bash
cd ~/code/local-ai/services/triposplat
./setup.sh
./start.sh
```

On first use, `setup.sh`:

1. stages the shared `../../.env` configuration and opens it in `$VISUAL`,
   `$EDITOR`, `vim`, `vi`, or `nano`, waiting for the editor to close before
   accepting the staged configuration;
2. does the same for the service-specific `.env`;
3. validates both files and atomically installs them with mode `0600`;
4. resolves and prints the exact host paths, model destinations, port, and GPU;
5. creates the service directories;
6. validates Docker, Compose, and NVIDIA prerequisites;
7. builds any service-local image required by the recipe;
8. provisions required/selected model weights and verifies their expected layout;
9. exits without starting the service.

`start.sh` never creates configuration or downloads model weights. If setup is
incomplete it stops and tells you to run `./setup.sh`.

Re-run configuration editing explicitly with:

```bash
./setup.sh --edit
```

For scripted/bootstrap use where the checked-in defaults are desired:

```bash
./setup.sh --accept-defaults
```

## Storage contract

Large data never lives in Git. The machine-wide defaults are configured once in
`local-ai/.env`:

```text
/data/services/hf-repos/         canonical explicitly downloaded model repos
/data/services/local-ai/         private mutable service state
/data/services/local-ai/workspaces/ intentionally shared project data
```

The default concrete service tree is:

```text
/data/services/local-ai/
├── comfyui/
│   ├── models/
│   ├── input/
│   ├── output/
│   ├── user/
│   ├── custom_nodes/
│   └── cache/
├── ace-step/
│   ├── models/
│   ├── outputs/
│   ├── huggingface/
│   ├── torch/
│   ├── uv-cache/
│   └── cache/
├── triposplat/
│   └── outputs/
├── tts/
│   └── benchmarks/       # retained TTS benchmark audio + metadata
└── workspaces/
```

Canonical externally downloaded model repositories stay separate:

```text
/data/services/hf-repos/
├── Comfy-Org/
│   └── MiniMax-H3/          # optional ComfyUI bundle
├── Serveurperso/
│   └── Qwen3-TTS-GGUF/      # qwentts talker + codec GGUFs
└── VAST-AI/
    └── TripoSplat/           # required TripoSplat weights
```

ACE-Step uses its explicit `models/` tree because upstream provides an
`acestep-download` command that provisions the runtime's expected checkpoint
layout. Its Hugging Face/Torch/uv caches remain separate and disposable.

See `docs/data-layout.md` for the retention and sharing rules.

## Model provisioning

Required and selected models are part of `./setup.sh`.

TripoSplat therefore needs only:

```bash
cd services/triposplat
./setup.sh
```

There is no separate download-before-setup step.

ComfyUI itself has no required model bundle. Optional bundles are selected in
`services/comfyui/.env`:

```dotenv
COMFYUI_MODEL_BUNDLES=minimax-h3
```

or for one setup invocation:

```bash
./setup.sh --with-model minimax-h3
```

TTS defaults to the qwentts 0.6B Q8 bundle and can additionally provision the
1.7B talker:

```bash
cd services/tts
./setup.sh --with-model qwen-1.7-customvoice-q8-gguf
```

Low-level model commands remain available for repair/maintenance, but refuse to
invent configuration:

```bash
services/triposplat/scripts/download_weights.sh
services/comfyui/scripts/download_minimax_h3_comfy.sh
```

If `.env` has not been initialized, they tell you to run `./setup.sh` first.
Hugging Face downloads run through `uvx --from huggingface_hub hf`, so a global
`hf` installation is not required.

## Configuration layering

The effective configuration is:

```text
checked-in service defaults
        ↓
local-ai/.env             machine-wide storage/network policy
        ↓
services/<name>/.env      service-specific choices/overrides
```

The root `.env` owns `HF_REPOS_ROOT`, `LOCAL_AI_SERVICE_ROOT`,
`LOCAL_AI_WORKSPACES_ROOT`, and `LOCAL_AI_BIND_ADDRESS`. Service `.env` files do
not duplicate those settings.

## Inspecting a service

Each service's doctor command prints its resolved storage/model plan and checks
required data:

```bash
./scripts/doctor.sh
```

From the repository root, inspect all configured services with:

```bash
./scripts/doctor.sh
```

## Engineering journal

Durable experiment measurements, failed approaches, and tentative conclusions
live under `dev/journals/`. See `dev/journals/gpt56.md` for the Qwen3-TTS GPU,
Pascal compatibility, and quantization experiments. These notes are intentionally
rawer than the user-facing service documentation so future maintainers can avoid
repeating expensive experiments.

## Repository policy

- Track the recipe required to recreate an experiment from the first useful run.
- Do not commit model weights, outputs, caches, `.env` files, or credentials.
- Bind web UIs to loopback by default. LAN exposure is an explicit setting.
- Prefer canonical model stores mounted read-only into services.
- Keep mutable state private to the service that owns it.
- Exchange data through an explicitly chosen workspace rather than mounting one
  service's private state into another.
- Keep service-specific work service-specific. Shared tooling stays limited to
  setup/lifecycle mechanics.
- Pin upstream revisions when rebuild reproducibility becomes important.
