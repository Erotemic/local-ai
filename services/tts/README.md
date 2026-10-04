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
pinned Kokoro baseline image. `start.sh` starts only Wavhost.

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
/data/local-ai/services/tts/benchmarks/20261004T191500Z-wavhost-qwen-0.6-customvoice-Ryan/
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

The service passes:

```dotenv
WAVHOST_QWEN_DTYPE=auto
```

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
/data/local-ai/services/tts/wavhost/.wavhost/
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

It is pinned to one selected GPU rather than exposing all GPUs. Wavhost can also
serve its own `kokoro` model for interface comparison, but that is an optional
model bundle and is not assumed to match the performance characteristics of the
specialized Kokoro-FastAPI GPU image.
