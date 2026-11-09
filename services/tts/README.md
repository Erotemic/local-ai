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

## Long-form lecture quality experiments

For opt-in Breeze long-form benchmarks, reference-pair diagnostics, and offline
regression tests, see [the long-form infrastructure guide](docs/breeze-longform-infrastructure.md).
The tools do not modify the production TTS endpoint or its lifecycle scripts.


### Breeze paragraph80 speech requests

The Breeze adapter supports opt-in paragraph-aware synthesis on the existing
`POST /v1/audio/speech` endpoint: pass `"long_form": true`, `"cfg_scale": 1.0`,
and omit speaking instructions. It groups complete sentences toward 80 words
per generation (110-word hard maximum), preserves paragraph boundaries,
retries overlong or implausibly brief passages at smaller sizes, and joins PCM
before the usual WAV/MP3 response conversion. It returns the response headers
`X-Local-AI-Speech-Policy: paragraph80-v1` and
`X-Local-AI-Speech-Timings` with passage offsets; `/v1/runtime` advertises the
policy. Single-request callers are unchanged unless they opt in.

For already-provisioned Breeze images, the new Python helper must be added to
the container image. After applying the source overlay, run:

```bash
cd ~/code/local-ai/services/tts
./compose.sh build breeze-tts2
TTS_ACTIVE_BACKEND=breeze-tts2 ./start.sh --keep-running
curl -fsS http://127.0.0.1:8821/v1/runtime | python3 -m json.tool
```

The example port is the currently published local port; check `docker ps` if
it changes. Building/restarting the Breeze container briefly interrupts
in-flight speech requests. Keep private voice identifiers and generated audio
outside Git.
Keep generated audio, reference recordings, and experiment data in private,
untracked storage.

## Quick start: Breeze and Kokoro together

From `services/tts/`, provision both backends, then start each without
stopping the other. Setup downloads the selected weights and images; startup
uses the provisioned files.

```bash
TTS_ACTIVE_BACKEND=breeze-tts2 ./setup.sh
TTS_ACTIVE_BACKEND=kokoro-cpu ./setup.sh
TTS_ACTIVE_BACKEND=breeze-tts2 ./start.sh --keep-running
TTS_ACTIVE_BACKEND=kokoro-cpu ./start.sh --keep-running
TTS_ACTIVE_BACKEND=breeze-tts2 ./test.sh --voice crall-math-dk
TTS_ACTIVE_BACKEND=kokoro-cpu ./test.sh
```

Use a voice available on your server in place of `crall-math-dk`. Breeze and
Kokoro CPU use ports 11441 and 8881 by default; `.env` can override these.
Kokoro GPU is a separate service on port 8880. Substitute `kokoro-gpu` in the
commands to use it, and stop it with `./compose.sh stop kokoro-gpu`. Use
`./compose.sh ps` to see running backends. Plain `./start.sh` stops the other
backends; `--keep-running` preserves them.

For Breeze's optional CUDA-graph path, set `TTS_BREEZE_FAST=1` when starting
it or in the service `.env`. Restarting replaces the Breeze container and
interrupts in-flight requests. Check `/v1/runtime` for `"breeze_fast": true`;
startup and first requests for new prompt sizes may need graph warmup. See the
benchmark notes below before comparing speed and audio quality.

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
Breeze TTS 2                 11441
Fish Audio S2 Pro            11442
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
    "model": "qwen-1.7-customvoice-q8-ggml",
    "input": "This is a local Qwen text to speech test. It tends to work better when the input is longer.",
    "voice": "ryan",
    "language": "English",
    "response_format": "mp3",
    "stream": false
  }' \
  --output qwen-test2.mp3
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
TTS_ACTIVE_BACKEND=kokoro-gpu ./setup.sh
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
TTS_ACTIVE_BACKEND=kokoro-cpu ./setup.sh
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
breeze-tts2
fish-s2-pro
```

To keep Breeze running while starting Kokoro on its separate port, provision
both backends first, then use `--keep-running`:

```bash
TTS_ACTIVE_BACKEND=breeze-tts2 ./setup.sh
TTS_ACTIVE_BACKEND=kokoro-gpu ./setup.sh
TTS_ACTIVE_BACKEND=breeze-tts2 ./start.sh --keep-running
TTS_ACTIVE_BACKEND=kokoro-gpu ./start.sh --keep-running
TTS_ACTIVE_BACKEND=breeze-tts2 ./test.sh --voice crall-math-dk
TTS_ACTIVE_BACKEND=kokoro-gpu ./test.sh
```

Without `--keep-running`, `start.sh` stops other TTS backends. Both GPU
backends share the GPU and need enough free VRAM. Use `kokoro-cpu` instead if
desired. Direct backend start scripts also leave other backends running.

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


### Tests

For Breeze:

./compose.sh build breeze-tts2
./setup.sh --with-model breeze-tts2
./start-candidate.sh breeze-tts2

curl -s http://127.0.0.1:8812/health
curl -s http://127.0.0.1:8812/v1/runtime

curl -sS -X POST http://127.0.0.1:8812/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "breeze-tts2",
    "input": "This is a direct benchmark test of Breeze TTS 2 on the RTX 3090.",
    "voice": "ryan",
    "response_format": "wav"
  }' \
  --output test-breeze.wav

curl -s http://127.0.0.1:8812/v1/audio/voices | python3 -m json.tool

curl -sS -X POST http://127.0.0.1:8812/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "breeze-tts2",
    "input": "This is a direct test of the Breeze TTS 2 voice clone running on my local RTX 3090.",
    "voice": "crall-math-dk",
    "response_format": "wav"
  }' \
  --output test-crall.wav && python3 scripts/audio_sanity.py test-crall.wav


curl -sS -X POST http://127.0.0.1:8812/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "breeze-tts2",
    "input": "(laugh) That algorithm converges surprisingly fast once the parameters are set correctly.",
    "voice": "crall-math-dk",
    "instruction": "Speak in an enthusiastic, engaging lecture style with clear articulation.",
    "response_format": "wav"
  }' \
  --output test-crall-lecture.wav

### Fish Audio S2 Pro

S2 Pro is an experimental candidate using the upstream Fish Speech runtime and
research-licensed model weights. Setup downloads the pinned model and builds its
image. Set `TTS_FISH_GPU` in `.env` to the host GPU index and optionally set
`TTS_FISH_PORT` (default 11442).

```bash
./setup.sh --with-model fish-s2-pro
./start-candidate.sh fish-s2-pro
curl -sS http://127.0.0.1:11442/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"fish-s2-pro","input":"This is a voice cloning test. How well can I speak in terms of mathematics here?","voice":"crall-math-dk","response_format":"wav"}' \
  -o test-fish.wav
python3 scripts/audio_sanity.py test-fish.wav
```

The voice needs matching `voice-references/crall-math-dk.wav` and `.txt`
files. If the audio check fails, inspect `test-fish.wav` for an error response.

To benchmark Breeze and Fish sequentially on an RTX 3090, first provision both
candidates with `./setup.sh --with-model breeze-tts2 --with-model fish-s2-pro`.
Then run:

```bash
./scripts/benchmark-3090.sh 3 0 crall-math-dk
```

Arguments are the number of requests per model, the physical GPU index, and the
reference voice. The runner checks the GPU name and requires matching WAV and
transcript files. It stops both candidate containers before starting, then stops
each after measuring it. The first request is identified separately from repeat
requests; startup timing includes the candidate readiness check. The session log
and each model's audio, request, timing, hardware and runtime metadata are kept
in a timestamped directory under `{TTS_DATA_ROOT}/benchmarks`. Review the audio
and the generated `LISTEN_REVIEW.md` alongside the performance numbers.

To measure Breeze's upstream CUDA graph fast path on the 3090, rebuild the
Breeze image after updating the adapter, then run:

```bash
./compose.sh build breeze-tts2
./scripts/benchmark-breeze-fast.sh 3 0 crall-math-dk
```

This is an opt-in alternative to the eager benchmark. It stops the Breeze and
Fish containers, starts Breeze with `TTS_BREEZE_FAST=1`, warms the upstream fast
profile before readiness, measures three requests, and stops Breeze afterward.
The startup time includes graph warmup; request timings do not. Artifacts are
retained in a separate timestamped `breeze-fast-3090` benchmark directory.
The runtime metadata records the mode. Prompt sizes outside the upstream warmup
profile are captured on first use, so the first request can include additional
graph capture time. Listen to the output as well as comparing
RTF and peak GPU memory with the eager baseline. Set
`TTS_BREEZE_FAST_START_TIMEOUT` if warmup needs more than 1800 seconds.

### Speech smoke test

After starting the service, run `./test.sh` to synthesize speech and check the
returned WAV for a plausible duration and audio level. The test discovers the
model and default voice where available. Use `./test.sh --voice crall-math-dk
--output test-crall.wav` to select a voice and retain the audio for listening.
`--model`, `--text`, and `--timeout` can also override the defaults. The test
checks the configured active backend and honors `TTS_*` environment overrides.


Long-form speech requests can use the asynchronous `/v1/audio/jobs` API to
observe completed paragraph counts, prioritize interactive playback, and
cancel between passages. See `docs/breeze-longform-infrastructure.md`.
