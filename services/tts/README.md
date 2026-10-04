# Local TTS service

This service makes local text-to-speech fit the same lifecycle as the other
`local-ai` recipes while keeping the first Qwen3-TTS run explicitly
experimental.

It provides two servers:

- **Wavhost** on `http://127.0.0.1:11435` by default. This is the primary
  service and the path for Qwen3-TTS plus other Wavhost models.
- **Kokoro-FastAPI** on `http://127.0.0.1:8880` by default. This preserves the
  existing accelerated GTX 1080 Ti-compatible Kokoro deployment as a known-good
  baseline rather than forcing the experiment to replace it.

Both expose `/v1/audio/speech` and can therefore be compared with the same
reader/client request shape.

## First 1080 Ti experiment

The default configuration intentionally provisions only:

```text
qwen-0.6-customvoice
```

Run:

```bash
./setup.sh
./start.sh
./scripts/benchmark.sh wavhost 2
```

`setup.sh` initializes the service config, builds Wavhost from the checked-out
`submodules/wavhost` source, downloads the selected Wavhost model, and pulls the
pinned Kokoro baseline image. Model provisioning passes Wavhost `--yes` after
showing the model license so setup never pauses for a second license prompt.
`start.sh` starts only Wavhost.

Start the Kokoro baseline separately when you want an A/B comparison:

```bash
./start-kokoro.sh
./scripts/benchmark.sh kokoro 2
```

The benchmark preserves every generated WAV plus `metadata.json`, `request.json`,
and `results.tsv` under `{TTS_DATA_ROOT}/benchmarks` by default. It deliberately
does **not** call the first request "cold": the script does not reset the server
or model cache, so the first request in a benchmark invocation may already be
warm. When `ffprobe` is available it reports real-time factor:

```text
RTF = generation wall time / generated audio duration
```

RTF below 1 means synthesis is faster than playback.

For example, a run creates a directory resembling:

```text
/data/services/local-ai/tts/benchmarks/20261004T191500Z-wavhost-qwen-0.6-customvoice-Ryan/
├── input.txt
├── metadata.json
├── request.json
├── results.tsv
├── run-01.wav
├── run-02.wav
└── run-03.wav
```

Set `TTS_BENCH_OUTPUT_DIR` if benchmark artifacts should live elsewhere.

## Why the Wavhost image is pinned this way

GTX 1080 Ti is Pascal (compute capability 6.1). There are two independent
compatibility constraints:

1. Current default PyTorch CUDA 13 builds do not contain pre-Turing kernels.
   The image therefore pins PyTorch 2.14.0 from the CUDA 12.6 (`cu126`) wheel
   index. PyTorch 2.14 is the final release line retaining that legacy binary
   target. TorchAudio is separately pinned to its maintenance release 2.11.0,
   which declares compatibility with future PyTorch versions; there is no
   TorchAudio 2.14 release to pair mechanically with PyTorch 2.14.
2. Wavhost previously loaded every CUDA Qwen model as BF16. Pascal has no native
   BF16 support. The submodule patch makes Qwen dtype selection hardware-aware:
   Ampere+ uses BF16, Volta/Turing uses FP16, and Pascal/Maxwell uses FP32.

Those are deliberately separate fixes: the container chooses a compatible
PyTorch binary, while Wavhost itself chooses a dtype the selected device can
execute.

The service explicitly runs Wavhost backends on its reserved GPU:

```dotenv
WAVHOST_DEVICE=cuda
WAVHOST_QWEN_DTYPE=auto
```

`WAVHOST_DEVICE` is a server-wide Wavhost policy. `auto` preserves each registry
model's recommendation; `cuda` makes HTTP-served Kokoro use the GPU as well as
Qwen. This local-ai recipe defaults to `cuda` because the container already has
a dedicated NVIDIA device reservation.

For the 1080 Ti, `auto` resolves to FP32. Consumer Pascal has weak FP16
arithmetic throughput, so FP32 is the conservative default. It is still useful
to test FP16 because it reduces memory pressure and this workload may behave
differently than a GEMM-only benchmark. Change the service `.env` and rebuild
only if you want to compare:

```dotenv
WAVHOST_QWEN_DTYPE=float16
```

No FlashAttention installation is attempted; FlashAttention 2 is not a Pascal
path.

Qwen's manual PyTorch/Triton path JIT-compiles a small native launcher on first
generation. The image therefore includes the minimal host compiler pieces
(`gcc` and `libc6-dev`) needed by Triton; omitting them causes generation to fail
with `Failed to find C compiler`.

Wavhost in this overlay also keeps model backends warm across HTTP requests.
That matters for the Android reader because it requests many consecutive text
chunks: without a cache, Wavhost recreated the backend and reloaded Qwen weights
for every chunk. The service uses:

```dotenv
WAVHOST_BACKEND_CACHE_SIZE=1
```

The one-entry LRU keeps the active Qwen model resident without assuming an
11 GB GPU can retain every installed TTS model at once. Set it to `0` to measure
fully cold behavior or increase it only when the models fit together.

## Model selection

The service config controls which Wavhost models setup provisions:

```dotenv
TTS_WAVHOST_MODELS=qwen-0.6-customvoice
```

Available model bundle names declared by this recipe are:

```text
qwen-0.6-customvoice
qwen-1.7-customvoice
qwen-0.6-base
qwen-1.7-base
kokoro
```

For example, after the 0.6B result is known, provision the 1.7B CustomVoice
model too with:

```bash
./setup.sh --with-model qwen-1.7-customvoice
```

or make it persistent in `.env`:

```dotenv
TTS_WAVHOST_MODELS=qwen-0.6-customvoice,qwen-1.7-customvoice
```

The Wavhost model store is persistent under:

```text
/data/services/local-ai/tts/wavhost/.wavhost/
```

by default. The Hugging Face/Torch caches are beneath the same service-owned
state tree and are disposable.

## GPU assignment

The defaults assume the two TTS servers can coexist on a two-GPU machine:

```dotenv
TTS_KOKORO_GPU=0
TTS_WAVHOST_GPU=1
```

Docker exposes exactly one GPU to each container. Inside Wavhost that selected
GPU is `cuda:0`, independent of its host index.

Swap the values if desired. They do not imply that the two 11 GB cards combine
into a 22 GB device; each service uses its assigned GPU independently.

## API smoke request

For Qwen:

```bash
curl http://127.0.0.1:11435/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen-0.6-customvoice",
    "input": "This is a local Qwen text to speech test.",
    "voice": "Ryan",
    "response_format": "mp3",
    "stream": false
  }' \
  --output qwen-test.mp3
```

For the preserved Kokoro server:

```bash
curl http://127.0.0.1:8880/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "kokoro",
    "input": "This is the same local text to speech test.",
    "voice": "af_heart",
    "response_format": "mp3",
    "speed": 1.0,
    "stream": false
  }' \
  --output kokoro-test.mp3
```

## Wavhost submodule workflow

The Docker image copies `submodules/wavhost` from the local checkout, so fixes
made on the fork are exercised by the service rather than waiting for an
upstream release. `setup.sh` initializes the submodule only if it is absent; it
does not reset an existing checkout with local work.

The Pascal change in this overlay is intentionally upstream-sized: dtype
selection and tests live in Wavhost. The PyTorch/CUDA pin remains a local-ai
service policy because it describes this particular deployment target.

## Kokoro parity

The compatibility server deliberately retains the image already in use:

```text
ghcr.io/remsky/kokoro-fastapi-gpu:v0.3.0-amd64
```

It is pinned to one selected GPU rather than exposing all GPUs. Wavhost can also serve its own `kokoro` model. This recipe sets
`WAVHOST_DEVICE=cuda`, so native Wavhost Kokoro uses the same reserved GPU rather
than the registry's CPU recommendation. Benchmark it independently from the
specialized Kokoro-FastAPI image:

```bash
./setup.sh --accept-defaults --with-model kokoro
./start.sh
./scripts/benchmark.sh wavhost-kokoro 3
./start-kokoro.sh
./scripts/benchmark.sh kokoro 3
```

## Quantized Q8_0 experiment with qwentts.cpp

The full-precision PyTorch baseline is established and should not be repeated
without a material runtime/code change. The next experiment uses qwentts.cpp as
a genuinely quantized runtime rather than loading compressed weights and
expanding them back to FP32 before generation.

The experimental service is deliberately separate from Wavhost:

```text
Wavhost / official PyTorch       http://127.0.0.1:11435
qwentts.cpp / Q8_0 GGUF          http://127.0.0.1:11436
Kokoro-FastAPI baseline          http://127.0.0.1:8880
```

qwentts.cpp's upstream `cuda12` image is documented as containing CUDA device
code from Pascal `sm_61` through newer architectures. Do not use its `cuda13`
image on the GTX 1080 Ti: CUDA 13 no longer compiles pre-Turing targets.

Provision only the quantized model bundle:

```bash
./setup.sh --with-model qwen-0.6-customvoice-q8-gguf
```

This downloads these two files from `Serveurperso/Qwen3-TTS-GGUF` into the
shared Hugging Face repository root:

```text
qwen-talker-0.6b-customvoice-Q8_0.gguf
qwen-tokenizer-12hz-Q8_0.gguf
```

The first experiment intentionally uses Q8_0 for both pieces because that is
the matching runtime's recommended deployment quant and avoids relying on BF16
execution on Pascal. Lower-bit Q4 is a later experiment only if Q8 is still too
slow and its audio quality remains acceptable.

Wavhost and qwentts.cpp both default to physical GPU 1, so they should not be
resident simultaneously on an 11 GB 1080 Ti. Keep the standalone Kokoro service
on GPU 0, but stop Wavhost before starting the quantized server:

```bash
./compose.sh stop wavhost
./start-qwentts.sh
./scripts/benchmark.sh qwentts 3
```

Use `./compose.sh`, not a bare `docker compose`, for direct lifecycle commands.
The wrapper always supplies both the machine-wide `../../.env` and service-local
`.env`; without it, a manual recreate can silently fall back to compose defaults
for paths such as `HF_REPOS_ROOT`. Caller environment variables still override
the files, so one-off experiments do not require editing persistent config.

The qwentts benchmark goes through the same `/v1/audio/speech` request path and
retains WAV/JSON/TSV artifacts under `{TTS_DATA_ROOT}/benchmarks`, so its RTF is
directly comparable to the earlier Wavhost measurements. It uses the same
lecture-like benchmark text and Ryan speaker.

The Pascal defaults also set:

```dotenv
TTS_QWENTTS_CLAMP_FP16=1
TTS_QWENTTS_NO_FA=0
```

qwentts.cpp documents clamping as protection for FP16 intermediate state on
sub-Ampere CUDA devices. Flash attention remains enabled for the first run.
Every qwentts setting can be overridden for a single start without editing
`.env`; `start-qwentts.sh` preserves explicit `TTS_QWENTTS_*` environment
variables after loading the configured files. For example, compare the same
model with FP16 clamping disabled via:

```bash
TTS_QWENTTS_CLAMP_FP16=0 ./start-qwentts.sh
./scripts/benchmark.sh qwentts 3
```

If initialization or synthesis fails specifically in the attention path, test
the no-flash-attention fallback independently:

```bash
TTS_QWENTTS_NO_FA=1 ./start-qwentts.sh
./scripts/benchmark.sh qwentts 3
```

`start-qwentts.sh` prints the effective GPU, image, host model paths, language,
`CLAMP_FP16`, and `NO_FA` before recreating the service. The benchmark then
reads the actual running container configuration and records those safe runtime
knobs in `metadata.json`, so a one-off override is not lost from the experiment
record.

After the experiment, restore the full-precision service if desired:

```bash
./compose.sh stop qwentts
./start.sh
```
