# ACE-Step service

Local ACE-Step 1.5 music/audio generation service with persistent host state and
optional API mode.

## Storage

The existing host-data default is retained:

```text
/data/service/docker/ace-step/
├── models/
├── outputs/
├── huggingface/
├── torch/
├── uv-cache/
└── cache/
```

Set `ACESTEP_DATA_ROOT` in `.env` to move it later without changing the Compose
file.

## Setup

```bash
cp .env.template .env
./setup.sh
```

The UI is available at `http://127.0.0.1:7860` by default.

## API

The API service is behind a Compose profile:

```bash
docker compose --profile api up -d ace-step-api
```

It binds to `http://127.0.0.1:8001` by default.

## Model/config selection

The default command uses:

```text
config:   acestep-v15-turbo
LM model: acestep-5Hz-lm-1.7B
```

Override `ACESTEP_CONFIG_PATH` or `ACESTEP_LM_MODEL_PATH` in `.env`.

## Audio format

The image retains the existing local patch that changes common ACE-Step UI
defaults from MP3 to FLAC. This avoids the TorchCodec MP3 export failure seen in
the original setup. WAV and other formats can still be selected in the UI.

## Reproducibility

`ACESTEP_REF` controls the upstream source checkout. It defaults to `main` while
this remains exploratory. Set it to a release tag or commit when you want a
particular working setup to rebuild identically.

## Diagnostics

```bash
./scripts/doctor.sh
```

## Verify the container environment

```bash
docker compose exec ace-step-ui bash -lc '
cd /opt/ACE-Step-1.5
uv run --no-sync python - <<PY
import torch, torchaudio
print("torch:", torch.__version__)
print("torchaudio:", torchaudio.__version__)
import torchcodec
print("torchcodec:", torchcodec.__version__)
import pytorch_wavelets
print("pytorch_wavelets: ok")
PY
'
```

Recent outputs remain under the service data root:

```bash
find "${ACESTEP_DATA_ROOT:-/data/service/docker/ace-step}/outputs" -type f | tail -20
```
