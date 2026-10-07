# Local TTS service

This service provides stable qwentts/Kokoro deployments plus opt-in candidate
experiments behind OpenAI-compatible HTTP APIs. The primary deployment target
is the GTX 1080 Ti; RTX 3090 runs are a comparison/render tier.

Operational baselines:

- **qwentts.cpp** for fast quantized Qwen3-TTS on NVIDIA GPUs;
- **Kokoro-FastAPI GPU** using the known-good Pascal-compatible image;
- **Kokoro-FastAPI CPU** for machines where no NVIDIA runtime should be needed.

Opt-in candidates: **IndexTTS 2.5**, **Chatterbox Flash**, and **Chatterbox Nano**.

There is intentionally no Wavhost layer. `local-ai` owns deployment and backend
selection directly.

## Endpoints

Default host ports are:

```text
qwentts.cpp raw engine      11436
qwentts client gateway      11437
Kokoro-FastAPI GPU           8880
Kokoro-FastAPI CPU           8881
IndexTTS 2.5                 11438
Chatterbox Flash             11439
Chatterbox Nano              11440
```

The qwentts gateway is the normal client endpoint. It proxies discovery,
performs bounded retries/plausibility checks, and converts qwentts WAV output to
MP3 with ffmpeg. The raw engine remains available for benchmarks and debugging.

## First setup

The default backend is qwentts with the 0.6B CustomVoice Q8 model:

```bash
./setup.sh
./start.sh
./status.sh
```

For a noninteractive setup using checked-in defaults:

```bash
./setup.sh --accept-defaults
```

The default model bundle is:

```text
qwen-0.6-customvoice-q8-gguf
```

Provision the 1.7B CustomVoice Q8 model as well with:

```bash
./setup.sh --accept-defaults --with-model qwen-1.7-customvoice-q8-gguf
```

or keep both selected in `.env`:

```dotenv
TTS_MODEL_BUNDLES=qwen-0.6-customvoice-q8-gguf,qwen-1.7-customvoice-q8-gguf
```

## qwentts on GTX 1080 Ti

The deployment uses the upstream CUDA 12 qwentts.cpp image and GGUF weights from
`Serveurperso/Qwen3-TTS-GGUF`.

The validated Pascal settings are:

```dotenv
TTS_QWENTTS_NO_FA=0
TTS_QWENTTS_CLAMP_FP16=0
```

`CLAMP_FP16=1` reproducibly corrupted synthesis on the tested GTX 1080 Ti, with
and without flash attention. Do not enable it on that deployment without a new
quality validation.

Normal Android/web clients should use the gateway on port 11437. The gateway is
a tiny local Python + ffmpeg image; it does not depend on another TTS runtime.

Example:

```bash
curl http://127.0.0.1:11437/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen-0.6-customvoice-q8-ggml",
    "input": "This is a local Qwen text to speech test.",
    "voice": "ryan",
    "language": "English",
    "response_format": "mp3",
    "stream": false
  }' \
  --output qwen-test.mp3
```

## Kokoro GPU

The GPU backend intentionally pins the image already verified on the GTX 1080
Ti:

```text
ghcr.io/remsky/kokoro-fastapi-gpu:v0.3.0-amd64
```

Select it persistently:

```dotenv
TTS_ACTIVE_BACKEND=kokoro-gpu
```

or start it once without changing `.env`:

```bash
TTS_ACTIVE_BACKEND=kokoro-gpu ./start.sh
```

Its default endpoint is:

```text
http://127.0.0.1:8880
```

and the GPU index is controlled by:

```dotenv
TTS_KOKORO_GPU=0
```

## Kokoro CPU

The CPU backend uses the matching pinned release without any NVIDIA device
reservation:

```text
ghcr.io/remsky/kokoro-fastapi-cpu:v0.3.0-amd64
```

Select it with:

```dotenv
TTS_ACTIVE_BACKEND=kokoro-cpu
```

or:

```bash
TTS_ACTIVE_BACKEND=kokoro-cpu ./start.sh
```

Its default endpoint is:

```text
http://127.0.0.1:8881
```

The separate port makes direct GPU-vs-CPU comparison possible when the direct
start scripts are used.

## Backend selection

Normal `./start.sh` operation is exclusive and accepts:

```text
qwentts
kokoro-gpu
kokoro-cpu
indextts25
chatterbox-flash
chatterbox-nano
```

Direct experiment starts are also available:

```bash
./start-qwentts.sh
./start-kokoro-gpu.sh
./start-kokoro-cpu.sh
./start-indextts25.sh
./start-chatterbox-flash.sh
./start-chatterbox-nano.sh
```

## LAN use

The service binds to loopback by default. For a trusted LAN server set:

```dotenv
TTS_BIND_ADDRESS=0.0.0.0
```

Then use `./status.sh` to print the selected backend's client URL. These
endpoints are unauthenticated; do not expose them to an untrusted network.

## Benchmarks

The common benchmark supports all active runtimes:

```bash
./scripts/benchmark.sh qwentts 5
./scripts/benchmark.sh kokoro-gpu 5
./scripts/benchmark.sh kokoro-cpu 5
./scripts/benchmark.sh indextts25 5
./scripts/benchmark.sh chatterbox-flash 5
./scripts/benchmark.sh chatterbox-nano 5
```

Artifacts are retained under `{TTS_DATA_ROOT}/benchmarks` by default and include
the input text, request JSON, runtime metadata, WAV files, and `results.tsv`.
When `ffprobe` is available the script reports real-time factor:

```text
RTF = generation wall time / generated audio duration
```

RTF below 1 means synthesis is faster than playback.

## Model storage

qwentts GGUF files use the shared canonical model repository:

```text
{HF_REPOS_ROOT}/Serveurperso/Qwen3-TTS-GGUF/
```

The Kokoro images contain/manage their own runtime assets and need no service
state volume for ordinary synthesis. TTS-private state is therefore limited to
retained benchmark artifacts.

## Experimental candidates: IndexTTS 2.5, Chatterbox Flash, Chatterbox Nano

Three newer runtimes are available as opt-in experiments without replacing the
known qwentts/Kokoro paths:

```text
indextts25         http://127.0.0.1:11438
chatterbox-flash   http://127.0.0.1:11439
chatterbox-nano    http://127.0.0.1:11440
```

Provision them explicitly:

```bash
./setup.sh --accept-defaults \
    --with-model indextts-2.5 \
    --with-model chatterbox-flash \
    --with-model chatterbox-nano
```

Then run the reproducible hardware matrix described in
[`EXPERIMENTS.md`](EXPERIMENTS.md). Every candidate exposes `/health`,
`/v1/models`, `/v1/audio/voices`, `/v1/voices`, `/v1/runtime`, and
`/v1/audio/speech`. `/v1/models` advertises the retained `ryan` voice through
`speakers` and `default_voice`, so generic clients can discover the correct
model/voice pair without backend-specific configuration. WAV and MP3 responses are
supported; benchmarks deliberately request WAV so transport transcoding does not
contaminate model RTF.

The default model selection key is now generic:

```dotenv
TTS_MODEL_BUNDLES=qwen-0.6-customvoice-q8-gguf
```

Existing `TTS_QWENTTS_MODELS` configuration is migrated automatically by
`setup.sh`.
