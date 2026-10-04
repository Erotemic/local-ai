# ACE-Step service

Local ACE-Step 1.5 music/audio generation service with persistent host state and
optional API mode.

## First use

```bash
./setup.sh
./start.sh
```

Setup initializes configuration, shows the exact storage plan, creates
persistent directories, builds the image, and explicitly runs ACE-Step's model
downloader. This prevents the normal upstream first-start auto-download from
choosing a checkpoint location before local-ai configuration exists.

Default resolved state:

```text
/data/services/local-ai/ace-step/
├── models/                 explicit ACE-Step checkpoints
├── outputs/                generated audio
├── huggingface/            disposable HF cache
├── torch/                  disposable Torch cache
├── uv-cache/               disposable uv cache
└── cache/                  disposable application cache
```

The required main bundle contains the VAE, Qwen3 embedding model,
`acestep-v15-turbo`, and `acestep-5Hz-lm-1.7B`. Setup uses upstream's
`acestep-download` command and verifies those directories before succeeding.

The UI defaults to `http://127.0.0.1:7860`.

## Reuse the existing pre-local-ai data

The scripts this service was migrated from used:

```text
/data/service/docker/ace-step
```

If that tree already contains your checkpoints and outputs, set this when the
setup editor opens `services/ace-step/.env`:

```dotenv
ACESTEP_DATA_ROOT=/data/service/docker/ace-step
```

Setup will inspect that tree and avoid downloading the required bundle again if
its expected directories are already present.

## Model/config selection

Defaults:

```text
config:   acestep-v15-turbo
LM model: acestep-5Hz-lm-1.7B
download: huggingface
```

Override `ACESTEP_CONFIG_PATH`, `ACESTEP_LM_MODEL_PATH`, or
`ACESTEP_DOWNLOAD_SOURCE` in the service `.env`.

## API

The API service remains behind its Compose profile. After normal setup:

```bash
set -a
source ../../.env
source ./.env
set +a
docker compose --profile api up -d --no-build ace-step-api
```

It binds to `http://127.0.0.1:8001` by default. The normal `./start.sh` starts the
UI service only.

## Audio format

The image retains the local patch changing common ACE-Step UI defaults from MP3
to FLAC, avoiding the TorchCodec MP3 export failure seen in the original setup.
WAV and other formats can still be selected in the UI.

## Start contract

`./start.sh` does not create configuration, download checkpoints, or build the
image. It verifies the required main bundle before starting.

## Reproducibility

`ACESTEP_REF` defaults to `main` while this remains exploratory. Set it to a
release tag or commit when a particular working setup should rebuild identically.
