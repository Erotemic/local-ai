# local-ai

Version-controlled recipes for local generative-AI services.

This repository is intentionally a collection rather than an orchestration
framework. Each service owns its Docker image, Compose configuration, setup,
and diagnostics. Shared conventions cover host storage, network exposure,
secrets, and explicit data exchange.

## Services

| Service | Purpose | Default UI |
| --- | --- | --- |
| `comfyui` | General ComfyUI installation; MiniMax H3 is one supported model family | `http://127.0.0.1:8188` |
| `ace-step` | ACE-Step music/audio generation | `http://127.0.0.1:7860` |
| `triposplat` | Single-image 3D Gaussian reconstruction | `http://127.0.0.1:7861` |

`infer-stack` is intentionally not included yet. If it belongs here later, it
can be added as another service without changing the role of this repository.

## Layout

```text
local-ai/
├── services/
│   ├── comfyui/
│   ├── ace-step/
│   └── triposplat/
├── scripts/
├── docs/
├── .env.template
└── README.md
```

Large data does not live in Git. The default host conventions are:

```text
/data/hf-repos/                 canonical model repositories
/data/service/<service>/        private mutable service state
/data/local-ai/workspaces/      explicitly shared project data
```

Existing service paths are preserved where practical. In particular, ACE-Step
continues to default to `/data/service/docker/ace-step` so adopting this repo
does not require moving its current data.

See `docs/data-layout.md` for ownership and sharing rules.

## First use

Initialize common directories:

```bash
cp .env.template .env
./scripts/init-data.sh
./scripts/doctor.sh
```

Then configure and start a service from its own directory:

```bash
cd services/comfyui
cp .env.template .env
./setup.sh
```

or:

```bash
cd services/ace-step
cp .env.template .env
./setup.sh
```

or:

```bash
cd services/triposplat
cp .env.template .env
./scripts/download_weights.sh
./setup.sh
```

## Repository policy

- Track the recipe required to recreate an experiment from the first useful run.
- Do not commit model weights, outputs, caches, `.env` files, or credentials.
- Bind web UIs to loopback by default. LAN exposure is an explicit setting.
- Prefer canonical model stores mounted read-only into services.
- Keep mutable state private to the service that owns it.
- Exchange data through an explicitly chosen workspace instead of mounting one
  service's private state into another.
- Keep service-specific work service-specific. Shared tooling should stay small.
- Pin upstream revisions when a setup becomes important enough that rebuild
  reproducibility is required.
