# Local TTS service

This service provides three explicitly separate local TTS runtimes behind
OpenAI-compatible HTTP APIs:

- **qwentts.cpp** for fast quantized Qwen3-TTS on NVIDIA GPUs;
- **Kokoro-FastAPI GPU** using the known-good Pascal-compatible image;
- **Kokoro-FastAPI CPU** for machines where no NVIDIA runtime should be needed.

There is intentionally no Wavhost layer. `local-ai` owns deployment and backend
selection directly.

## Endpoints

Default host ports are:

```text
qwentts.cpp raw engine      11436
qwentts client gateway      11437
Kokoro-FastAPI GPU           8880
Kokoro-FastAPI CPU           8881
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
TTS_QWENTTS_MODELS=qwen-0.6-customvoice-q8-gguf,qwen-1.7-customvoice-q8-gguf
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
```

Direct experiment starts are also available:

```bash
./start-qwentts.sh
./start-kokoro-gpu.sh
./start-kokoro-cpu.sh
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
