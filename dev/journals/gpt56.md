# GPT-5.6 engineering journal

This file is a durable engineering journal for experiments, failed approaches,
measurements, and tentative conclusions produced while working on `local-ai`.
It is intentionally more verbose than user-facing documentation. The goal is to
preserve enough raw evidence that a future maintainer can reconstruct why a
choice was made without repeating expensive downloads, builds, or GPU runs.

Measurements here are observations, not promises. When a run was not controlled
tightly enough to support a conclusion, that ambiguity is recorded explicitly.

Structured TTS measurements are also maintained in `dev/benchmarks/tts_measurements.csv`
(one row per benchmark run), with a compact canonical summary in
`dev/benchmarks/tts.md`. The journal remains the place for chronology, failed
paths, interpretation, and decisions; the benchmark ledger is the place to add
future numeric observations so averages can be recomputed from raw rows.

## 2026-10-04 — Local TTS, Qwen3-TTS, Wavhost, and Pascal

### Goal

Evaluate Qwen3-TTS as a higher-quality local lecture-reading backend while
preserving the existing accelerated Kokoro service. The target deployment has
2x GTX 1080 Ti GPUs, so the experiment had to answer both:

1. Can current Qwen3-TTS software execute at all on Pascal / compute capability
   6.1?
2. If it executes, is generation fast enough to be useful for an Android reader
   that pre-generates chunks while previous audio plays?

The development machine has 2x RTX 3090. The production-ish test machine
(`Ooo`) has 2x GTX 1080 Ti and several unrelated services already running.
Wavhost was added as `submodules/wavhost` and maintained through an Erotemic
fork while retaining the original repository as an upstream remote locally.

### Stable experiment shape

The service under test was:

- API/runtime: Wavhost, built from the checked-out submodule.
- Model: `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`.
- Wavhost model ID: `qwen-0.6-customvoice`.
- Voice: `Ryan`.
- Qwen package: `qwen-tts==0.1.1`.
- PyTorch: `2.14.0+cu126`.
- TorchAudio: `2.11.0+cu126`.
- CUDA runtime in the container: 12.6.
- FlashAttention: not installed; all measurements below use the portable/manual
  PyTorch attention path.
- Output: 24 kHz WAV.
- RTF definition: `wall_clock_seconds / generated_audio_seconds`; RTF < 1 is
  faster than real time.

The benchmark text was:

> In this lecture, we will examine how a numerical algorithm transforms a
> sequence of approximations into a stable solution. The important point is not
> only the final answer, but also the assumptions that make each intermediate
> step valid. We will keep those assumptions explicit as we proceed.

Current `services/tts/scripts/benchmark.sh` stores the text, JSON request, WAVs,
metadata, and timing TSV so future runs should preserve the raw artifacts rather
than only stdout.

### Problems encountered before useful measurements

#### Wavhost selected BF16 unconditionally on CUDA

The upstream Qwen backend initially selected `torch.bfloat16` for every CUDA
GPU. That is not a safe policy for Pascal. The fork was changed to select
precision from compute capability and to provide `WAVHOST_QWEN_DTYPE` as an
explicit override.

Current policy established during this work:

- Pascal and older supported CUDA devices: FP32.
- Volta/Turing: FP16.
- Ampere and newer: BF16.
- Explicit override remains available for experiments.

An important observation from the GTX 1080 Ti test is that
`torch.cuda.is_bf16_supported()` returned `True` even though the card reported
compute capability `(6, 1)`. Therefore the implementation deliberately does
not treat that API as authoritative for the precision policy.

#### Triton required a C compiler at runtime

The first Qwen generation on the 3090 reached model generation and then failed:

```text
Speech generation failed: Failed to find C compiler. Please specify via CC
environment variable or set triton.knobs.build.impl.
```

Installing `gcc` and `libc6-dev` in the running container fixed the error. These
packages were then made permanent dependencies of the TTS Wavhost image.

This is a runtime/JIT requirement, not merely a source-build dependency. A
container that successfully installs all Python packages can still fail on the
first generation if it lacks the compiler.

#### Wavhost recreated backends per request

The server originally constructed a fresh backend for each speech request.
Qwen lazily loads the model inside the backend, so this would reload weights for
reader chunks. The fork now maintains a configurable LRU backend cache; the
default service keeps one model resident.

#### Backend INFO logging was invisible

Wavhost configured the CLI logger but backend modules use sibling loggers such
as `wavhost.backends`. Their INFO messages were therefore absent from server
logs, which initially made model-cache verification confusing. Logging was
changed to configure the Wavhost package logger once at entry points.

#### Benchmark artifacts were initially discarded

The first benchmark script used temporary WAV files and removed them after
measuring duration. That made subjective comparison and later forensic review
impossible. The current script retains a timestamped experiment directory with:

```text
input.txt
metadata.json
request.json
results.tsv
run-01.wav
run-02.wav
...
```

The script also uses `phase=first` / `phase=repeat`, not `cold` / `warm`, unless
cache state is actually controlled. The benchmark itself does not reset the
server/model cache.

### RTX 3090 observations

Container introspection on the development machine:

```text
torch = 2.14.0+cu126
torchaudio = 2.11.0+cu126
qwen-tts = 0.1.1
torch CUDA runtime = 12.6
compiled CUDA archs = ['sm_50', 'sm_60', 'sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90']
visible GPU = NVIDIA GeForce RTX 3090
capability = (8, 6)
bf16 supported = True
```

The wheel containing `sm_60` mattered because the eventual Pascal card was
compute capability 6.1. The actual 1080 Ti run later proved that this wheel was
usable there.

#### Initial 3090 run, automatic BF16

After fixing the missing compiler, one recorded run under the automatic 3090
policy produced:

| Run | Phase at the time | Wall (s) | Audio (s) | RTF | Bytes |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | first / likely cold-ish | 40.121 | 21.280 | 1.885 | 1,021,484 |
| 2 | repeat | 32.548 | 18.800 | 1.731 | 902,444 |
| 3 | repeat | 35.377 | 20.000 | 1.769 | 960,044 |

Mean repeat RTF: **1.750**.

This already showed that the stock/manual PyTorch Qwen path did not synthesize
faster than real time on the 3090.

#### Chronology-ambiguous 3090 run

The following numbers were captured during the transition to an explicit FP32
configuration. The operator believed this run was already FP32, but the pasted
shell transcript was out of chronological order. Preserve the numbers, but do
**not** use them as the canonical precision comparison:

| Run | Wall (s) | Audio (s) | RTF | Bytes |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 36.054 | 19.760 | 1.825 | 948,524 |
| 2 | 34.936 | 22.160 | 1.577 | 1,063,724 |
| 3 | 33.321 | 20.480 | 1.627 | 983,084 |

This row exists specifically so a future reader does not mistake these numbers
for a missing experiment or try to infer a dtype from transcript ordering.

#### Confirmed 3090 FP32 run

The environment was explicitly verified first:

```text
WAVHOST_QWEN_DTYPE=float32
```

Then the benchmark produced:

| Run | Benchmark phase | Wall (s) | Audio (s) | RTF | Bytes |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | first | 28.305 | 18.560 | 1.525 | 890,924 |
| 2 | repeat | 30.234 | 19.680 | 1.536 | 944,684 |
| 3 | repeat | 30.477 | 19.280 | 1.581 | 925,484 |

Mean of all three RTFs: **1.547**.  Mean repeat RTF: **1.559**.

The model had already served requests in this container, so `first` here should
not be interpreted as a true cold load.

FP32 appeared faster than the earlier BF16/manual-attention run in this small
sample. Do not infer that FP32 is intrinsically faster on a 3090 from these
measurements: Qwen generation is stochastic, output durations varied, cache
state was not reset identically, and this was not designed as a precision
microbenchmark.

A later `nvidia-smi` snapshot showed `/usr/local/bin/python` using 6,776 MiB on
GPU 0 after TTS requests. This is consistent with the resident Wavhost/Qwen
process, but the captured transcript did not explicitly map that PID to the
container, so treat the attribution as likely rather than proven.

### GTX 1080 Ti observations

The `Ooo` test machine already had Kokoro and other services running. GPU 0 was
busy while GPU 1 was essentially empty, so Wavhost was pinned to physical GPU 1
instead of stopping the existing Kokoro service.

The exact image built successfully on `Ooo`. Container introspection showed:

```text
torch = 2.14.0+cu126
torch CUDA = 12.6
archs = ['sm_50', 'sm_60', 'sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90']
GPU = NVIDIA GeForce GTX 1080 Ti
capability = (6, 1)
BF16 = True
```

Again, the final line is why the capability-based dtype policy is retained.
The actual experiment explicitly set:

```text
TTS_WAVHOST_GPU=1
WAVHOST_QWEN_DTYPE=float32
```

The service started, `/health` returned `{"status":"ok"}`, and Qwen generated
valid audio on Pascal. This is the central compatibility result: **stock
Qwen3-TTS 0.6B through the patched Wavhost/PyTorch path works on a GTX 1080 Ti
in FP32.**

#### Raw GTX 1080 Ti FP32 benchmark

The retained experiment directory from this run was printed as:

```text
/data/services/local-ai/tts/tts/benchmarks/20261004T195413Z-wavhost-qwen-0.6-customvoice-Ryan
```

The doubled `tts/tts` was caused by temporarily setting
`LOCAL_AI_SERVICE_ROOT=/data/services/local-ai/tts` while the service itself
appends `/tts`. The repository defaults were subsequently corrected to make
`LOCAL_AI_SERVICE_ROOT=/data/services/local-ai`; fresh runs therefore resolve
to `/data/services/local-ai/tts/...`.

Raw timing data:

| Run | Benchmark phase | Wall (s) | Audio (s) | RTF | Bytes |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | first | 84.105 | 20.880 | 4.028 | 1,002,284 |
| 2 | repeat | 76.170 | 23.040 | 3.306 | 1,105,964 |
| 3 | repeat | 65.744 | 20.160 | 3.261 | 967,724 |

Mean repeat RTF: **3.284**.

The first request was the first benchmark request after starting the service and
was substantially slower, so it is plausibly paying additional lazy-init/cache
cost. The benchmark did not itself prove a pristine cold state, so the raw label
remains `first` rather than `cold`.

Compared with the confirmed 3090 FP32 repeat mean (1.559), the 1080 Ti FP32
repeat mean (3.284) was about **2.11x the RTF**. Separately, the 1080 Ti requires
about **3.3 seconds of compute per second of generated audio**, which is the more
important application-level limitation.

### Subjective quality

Ryan was listened to directly and judged good enough to continue investigating
Qwen rather than spending more time on voice-selection experiments. No formal
MOS or blind A/B test was performed. The decision was simply that voice quality
was no longer the blocking question; serving performance became the next target.

### Tentative conclusions from the full-precision experiments

1. **Pascal compatibility is established.** The patched Wavhost + PyTorch
   2.14/cu126 stack can load and generate Qwen3-TTS 0.6B CustomVoice on a GTX
   1080 Ti.
2. **Full-precision serving is not fast enough for the desired steady-state
   reader workload.** The confirmed 3090 FP32 run was ~1.56 RTF and the 1080 Ti
   repeat runs were ~3.28 RTF. Prefetch can hide some latency, but neither result
   is faster than real time.
3. **Do not spend compute repeating the same FP32 compatibility experiment**
   unless code/runtime changes materially. These numbers are now the baseline.
4. **Quantized execution is the next useful line of investigation.** The target
   should be genuinely quantized inference kernels/runtime, not merely compressed
   weights that are expanded back to FP32 before generation.
5. **Keep Kokoro as the operational fallback/baseline.** There was no need to
   take it down to test Qwen because the two services can be pinned to different
   physical GPUs.

### Quantization / alternate-runtime survey (2026-10-04)

This section records external candidates found after the baseline experiment.
These are research notes, not locally validated results.

#### Important architecture point: quantization is not currently a drop-in Wavhost repo swap

The current Wavhost Qwen backend calls `qwen_tts.Qwen3TTSModel.from_pretrained`
and its built-in registry describes the official Qwen safetensor checkpoint
layout. GGUF, MLX, OpenVINO, and ONNX artifacts use different execution engines.
Changing only the Hugging Face repository name is therefore not a sound generic
quantization interface.

The desired end state is still for clients to see one Wavhost/OpenAI-compatible
TTS API. Wavhost should select an implementation backend from the model entry:
for example, official PyTorch Qwen versus a GGUF/ggml Qwen runtime. Quantization
and runtime should be explicit in model identity/metadata so measurements remain
reproducible rather than silently replacing what `qwen-0.6-customvoice` means.

A reasonable future naming shape would be conceptually similar to:

```text
qwen-0.6-customvoice             # official/PyTorch baseline
qwen-0.6-customvoice-q8-ggml     # explicit Q8 GGUF runtime
qwen-0.6-customvoice-q4-ggml     # only if quality/speed tradeoff is acceptable
```

The exact names are not decided; the invariant is that runtime/quantization must
not be a hidden mutable property of an existing model ID.

#### Candidate 1 — Q8_0 GGUF via `qwen3-tts-ggml`: highest-priority next test

Model/runtime:

- https://huggingface.co/sakasegawa/qwen3-tts-ggml
- Q8_0 0.6B CustomVoice talker: ~968 MB.
- F16 12 Hz codec: ~246 MB.
- Runtime supports Metal, Vulkan, CUDA, and CPU according to its model card.
- Same nine CustomVoice speakers, including Ryan.

The model card reports the following **external** NVIDIA result for the 0.6B
Q8_0 model after shader compilation:

```text
RTX 2080 / Vulkan
first audio: 0.07 s
RTF:         0.31
VRAM:        1.6 GB
```

That is not a GTX 1080 Ti result and should not be projected directly onto
Pascal. It is nevertheless the strongest candidate because it demonstrates
sub-real-time Qwen3-TTS on an older NVIDIA generation with a genuinely quantized
GGML runtime. The GTX 1080 Ti supports Vulkan, so this should be the **first
quantized experiment**.

Start with Q8 rather than Q4. VRAM is not our binding constraint at 11 GB, and
Q8 minimizes quality risk while testing whether the runtime change solves the
performance problem.

#### Candidate 2 — Q8_0 GGUF via CrispASR: strong second/runtime-integration candidate

Sources:

- https://huggingface.co/cstr/qwen3-tts-0.6b-customvoice-GGUF
- https://github.com/CrispStrobe/CrispASR
- https://github.com/CrispStrobe/CrispASR/blob/main/docs/tts.md

The cstr CustomVoice conversion recommends a ~968 MB Q8_0 talker and uses a
separate GGUF 12 Hz tokenizer/codec. Its model card reports good ASR round-trip
checks for multiple speakers. CrispASR advertises Qwen3-TTS with CUDA/Metal
acceleration and is a broader C++ GGML runtime hub.

This is a good second experiment, and perhaps a better long-term Wavhost backend
if its API/library boundary is easier to integrate than the standalone
`qwen3-tts-ggml` runtime. Do not assume the two GGUF layouts are interchangeable;
GGUF is a container format, and both projects document runtime-specific model
layouts.

#### Lower priority — custom affine 4-bit safetensors / cakel loader

Sources:

- https://github.com/cakel/Qwen3-TTS-4bit
- https://huggingface.co/Wookidooki/Qwen3-TTS-12Hz-0.6B-CustomVoice-4bit

This path stores the 0.6B model in a custom packed 4-bit representation, but the
loader is explicitly `load_4bit_dequant`: it dequantizes and caches a full
BF16/FP32 representation for use by the normal Qwen runtime. The published
storage notes list a ~3.4 GB FP32 dequantized cache on Pascal-class hardware.

Therefore this should **not be our first performance experiment**. It may reduce
download/storage size or solve some load-memory constraints, but it does not by
itself demonstrate quantized arithmetic through the generation hot path. The
project also warns of long-sentence pronunciation/prosody degradation. If we
later test it, measure runtime rather than assuming "4-bit" implies faster
inference.

#### Not targeted at these NVIDIA machines

- AtomGradient / MLX 4-bit and pruned-vocabulary models are optimized for Apple
  Silicon. Interesting compression work, but not the 1080 Ti runtime target.
  https://huggingface.co/AtomGradient/Qwen3-TTS-0.6B-CustomVoice-4bit-pruned-vocab-lite
- Arm mixed-precision/LiteRT artifacts target Arm/mobile execution. They are not
  the first choice for x86 + NVIDIA.
  https://huggingface.co/Arm/qwen3-tts-0-6b-custom-voice-mix-precision
- AtomGradient's OpenVINO INT8 package targets Intel Core Ultra hardware.
  https://huggingface.co/AtomGradient/Qwen3-TTS-12Hz-0.6B-CustomVoice-streaming-int8-ov
- MLX model variants from other publishers are also Apple-specific.

#### ONNX: keep on the list, but after GGML

There are active ONNX conversions, including:

- https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice
- https://huggingface.co/elbruno/Qwen3-TTS-12Hz-0.6B-CustomVoice-ONNX

ONNX Runtime could eventually be interesting if a CUDA execution-provider path
benchmarks well on Pascal. The examined elbruno export is ~5.9 GB and is not a
compact quantized artifact, and no comparable GTX/Pascal throughput result was
found during this survey. It therefore follows the Q8 GGML experiments rather
than preceding them.

### Proposed next experiment matrix

Do not change the existing PyTorch baseline while testing new runtimes. Preserve
it so regressions and quality differences have a stable reference.

Use the same benchmark text and Ryan voice when possible, retain every WAV, and
record runtime/backend explicitly.

| Priority | Hardware | Runtime/model | Quant | What this answers |
| ---: | --- | --- | --- | --- |
| 1 | GTX 1080 Ti | `qwen3-tts-ggml` CustomVoice | Q8_0 | Can a genuinely quantized Vulkan path beat RTF 1 on Pascal? |
| 2 | RTX 3090 | same `qwen3-tts-ggml` artifact | Q8_0 | Cross-check runtime behavior and establish Ampere Q8 baseline. |
| 3 | GTX 1080 Ti | CrispASR CustomVoice | Q8_0 | Is its CUDA/Vulkan integration faster/easier to serve? |
| 4 | either | GGML Q4, only if available/needed | Q4 | Additional speed/memory vs audible quality loss. |
| 5 | GTX 1080 Ti | ONNX Runtime candidate | varies | Does ORT offer a better Pascal kernel path than PyTorch? |

For each run record at minimum:

- exact model repository + revision/hash;
- exact runtime repository + revision/hash;
- quantization format;
- device/backend (CUDA vs Vulkan vs CPU);
- first-audio latency if streaming is supported;
- wall time, generated audio duration, and RTF;
- peak and resident VRAM;
- model load time;
- exact text, speaker, sampling parameters, and seed/determinism if supported;
- retained output WAV for quality comparison.

Qwen sampling produces different durations between runs, so compare RTF rather
than wall time alone. For runtime parity work, also add a deterministic/greedy
case if both engines support it; keep the default-sampling benchmark because it
matches the intended application more closely.

### Storage-root observation: workspaces nested under `LOCAL_AI_SERVICE_ROOT`

Current defaults after the path cleanup are:

```text
HF_REPOS_ROOT=/data/services/hf-repos
LOCAL_AI_SERVICE_ROOT=/data/services/local-ai
LOCAL_AI_WORKSPACES_ROOT=/data/services/local-ai/workspaces
```

At present, nesting `LOCAL_AI_WORKSPACES_ROOT` under `LOCAL_AI_SERVICE_ROOT` is
**not a technical problem** in this repository. `scripts/local_ai.py` does not
enumerate every child of `LOCAL_AI_SERVICE_ROOT` and treat it as a disposable
service. Service manifests explicitly construct paths such as
`{LOCAL_AI_SERVICE_ROOT}/tts`, `{LOCAL_AI_SERVICE_ROOT}/comfyui`, etc. The
workspace variable is separately configured.

There is, however, a semantic/operational footgun: the name
`LOCAL_AI_SERVICE_ROOT` suggests that the entire tree is private service-owned
state, while `workspaces/` is intentionally cross-service/user data with a
different retention policy. A future blanket backup, rsync, cleanup, or
`rm -rf "$LOCAL_AI_SERVICE_ROOT"` could conflate those classes.

Tentative decision: **leave the current nesting in place for now** because it is
simple, no current code recursively cleans that root, and it matches the desired
`/data/services/...` organization. Maintain the invariant that root-level
cleanup must never assume every child of `LOCAL_AI_SERVICE_ROOT` is disposable.
If this distinction starts causing code or operational complexity, prefer
renaming the parent concept (for example a broader local-ai data root) over
adding another awkward `/services` directory merely to satisfy the variable
name.

### Reproduction / avoid-duplicate-compute note

Before rerunning full-precision Qwen3-TTS 0.6B on these GPUs, compare the
intended change against these baselines:

```text
RTX 3090, PyTorch/manual attention, confirmed FP32:
    repeat RTF ~1.56

GTX 1080 Ti, PyTorch/manual attention, FP32:
    repeat RTF ~3.28
```

A new experiment should normally be justified by a material change in runtime,
quantization, kernels, model revision, sampling strategy, or serving pipeline.
If none of those changed, repeating the same benchmark is unlikely to teach us
more than the retained artifacts already do.

## 2026-10-04: concrete Q8_0 experiment recipe

The first quantized experiment was wired into `services/tts` after the FP32
baselines above were established. This is deliberately a new backend rather
than a Wavhost checkpoint swap because GGUF requires a different execution
runtime.

Selected runtime: `ServeurpersoCom/qwentts.cpp`, served by its upstream
OpenAI-compatible `tts-server`. The local-ai recipe uses:

```text
image: ghcr.io/serveurpersocom/qwentts.cpp:cuda12
port:  11436
GPU:   physical GPU 1
alias: qwen-0.6-customvoice-q8-ggml
```

Upstream's Docker documentation states that the `cuda12` image is built for a
wide architecture range including Pascal `sm_61`; the `cuda13` image is not a
Pascal option. This makes the prebuilt CUDA-12 image preferable to a bespoke
local build for the first experiment.

Selected model bundle, from `Serveurperso/Qwen3-TTS-GGUF`:

```text
qwen-talker-0.6b-customvoice-Q8_0.gguf   ~969 MB
qwen-tokenizer-12hz-Q8_0.gguf            ~291 MB
```

The matching model card calls Q8_0 the recommended default. Start here rather
than Q4_K_M: the existing 11 GB card is not VRAM-bound by a ~1.3 GB quantized
model pair, and Q8 gives a lower-risk quality comparison. If Q8 performance is
still inadequate, Q4_K_M becomes meaningful follow-up compute.

Pascal-specific runtime defaults:

```text
CLAMP_FP16=1
NO_FA=0
```

The clamp follows qwentts.cpp's own ABI documentation: it guards FP16 hidden
state/intermediate behavior on sub-Ampere CUDA targets. Flash attention remains
on initially; `NO_FA=1` is the first fallback if attention-specific errors occur.

Exact first-run recipe:

```bash
cd ~/code/local-ai/services/tts
./setup.sh --with-model qwen-0.6-customvoice-q8-gguf

docker compose stop wavhost
./start-qwentts.sh
./scripts/benchmark.sh qwentts 3
```

Do **not** stop the standalone Kokoro baseline just for this experiment when it
remains pinned to the other GPU. Wavhost and qwentts.cpp do share GPU 1 by
default, so only one of those two should be resident during the 1080 Ti timing
run.

The benchmark uses the same retained-artifact convention as the PyTorch runs.
Record the resulting `results.tsv`, generated WAVs, image identity, and whether
`NO_FA` had to be changed before drawing conclusions. The principal threshold
is whether Q8_0 gets below RTF 1.0; the prior 1080 Ti FP32 repeat baseline is
RTF 3.284.

### Provisioning note: multiple Hugging Face include patterns

The first Q8 provisioning attempt exposed a generic bug in `scripts/local_ai.py`:
for a manifest with two `include` entries it emitted one `--include` followed by
both values. The `hf download` CLI accepts one pattern per `--include`, so the
second GGUF was parsed as a positional filename. The CLI warned that `--include`
was being ignored and downloaded only the tokenizer, leaving the talker missing.

Observed partial result:

```text
Downloaded: qwen-tokenizer-12hz-Q8_0.gguf (~291 MB)
Missing:    qwen-talker-0.6b-customvoice-Q8_0.gguf
```

The generic downloader now repeats `--include` once per manifest pattern. This
also protects other multi-file Hugging Face bundles (for example MiniMax H3)
from the same parsing bug. Re-running provisioning is incremental because
`--local-dir` metadata and already-present files are reused.


### 2026-10-04: first qwentts.cpp Q8 startup attempt exposed a readiness race

After fixing multi-file Hugging Face provisioning, both Q8_0 files were present on the GTX 1080 Ti host:

- `qwen-talker-0.6b-customvoice-Q8_0.gguf`: 924 MiB as reported by `ls -lh`.
- `qwen-tokenizer-12hz-Q8_0.gguf`: 278 MiB as reported by `ls -lh`.

The first `./start-qwentts.sh` invocation pulled `ghcr.io/serveurpersocom/qwentts.cpp:cuda12` and returned immediately after Docker reported the container started. An external `/health` request made immediately afterward failed with curl exit 56 (`Recv failure: Connection reset by peer`), and an immediately-following benchmark failed the same way. The benchmark harness then attempted to rename a WAV that curl had never created, producing a secondary `mv: cannot stat ... run-01.wav` error.

Do **not** interpret this attempt as a qwentts inference failure yet. The start helper did not wait for model/CUDA initialization or health before returning, so the requests may simply have raced startup. Upstream's CUDA-12 image documents Pascal `sm_61` support, and upstream issue #35 includes a GTX 1080 Ti log reaching `[Server] listening on 0.0.0.0:8080` with the same CUDA image family, so Pascal support is independently evidenced:

- https://github.com/ServeurpersoCom/qwentts.cpp/blob/master/docs/DOCKER.md
- https://github.com/ServeurpersoCom/qwentts.cpp/issues/35

Follow-up harness changes:

- `start-qwentts.sh` now waits for `/health`, detects an exited/restarting container, and emits the tail of qwentts logs on failure.
- `benchmark.sh` refuses to benchmark an unhealthy Wavhost/qwentts server and records curl transport failures without assuming an output WAV exists. It saves a container-log snapshot beside failed benchmark artifacts.

This preserves the first attempt as an infrastructure/readiness observation rather than contaminating the Q8 performance baseline with a startup race.

### 2026-10-04: Q8_0 qwentts.cpp results on GTX 1080 Ti and RTX 3090

The readiness-hardened qwentts path subsequently reached `/health` on the GTX
1080 Ti and produced valid audio. This closes the earlier startup-race question:
the upstream CUDA-12 image and the selected Q8_0 model bundle do run on Pascal.

#### GTX 1080 Ti / qwentts.cpp / Q8_0

Runtime configuration at the time of this benchmark:

```text
backend:       qwentts.cpp CUDA-12 image
model:         qwen-talker-0.6b-customvoice-Q8_0.gguf
codec:         qwen-tokenizer-12hz-Q8_0.gguf
voice:         ryan
physical GPU:  1 (GTX 1080 Ti)
CLAMP_FP16:    1
NO_FA:         0
```

Retained artifact directory printed by the benchmark:

```text
/data/services/local-ai/tts/tts/benchmarks/20261004T214108Z-qwentts-qwen-0.6-customvoice-q8-ggml-ryan
```

The doubled `tts/tts` here is historical host configuration from before the
storage-root default cleanup; it is not part of the runtime result.

Raw timing data:

| Run | Phase | Wall (s) | Audio (s) | RTF | Bytes |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | first | 6.949 | 27.600 | 0.252 | 1,324,844 |
| 2 | repeat | 6.272 | 25.200 | 0.249 | 1,209,644 |
| 3 | repeat | 5.531 | 23.120 | 0.239 | 1,109,804 |

Mean repeat RTF: **0.244**, or about **4.10x real-time throughput**.

Compared with the earlier Wavhost/PyTorch FP32 repeat mean of 3.284 on the same
class of GPU, the observed end-to-end RTF improved by about **13.5x**. This is
**not a quantization-only speedup claim**: both quantization and execution
runtime changed at once (PyTorch/Qwen package -> qwentts.cpp/GGML CUDA).

This result changes the practical conclusion for the 1080 Ti. Full-precision
Qwen was too slow for steady-state reader use, but the Q8_0 qwentts.cpp path is
comfortably faster than real time and is now a viable deployment candidate,
subject to acceptable audio quality and operational stability.

#### RTX 3090 / qwentts.cpp / Q8_0, clamp enabled

The first matching 3090 measurement used physical GPU 0 and the same Q8_0
runtime/model. The retained artifact directory was:

```text
/data/local-ai/services/tts/benchmarks/20261004T224446Z-qwentts-qwen-0.6-customvoice-q8-ggml-ryan
```

This development machine still had an older explicit `TTS_DATA_ROOT` at the
time of the run, hence `/data/local-ai/...` rather than the newer repository
default `/data/services/...`. Do not interpret that path difference as a runtime
difference.

Raw timing data:

| Run | Phase | Wall (s) | Audio (s) | RTF | Bytes |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | first | 10.355 | 62.320 | 0.166 | 2,991,404 |
| 2 | repeat | 12.510 | 76.640 | 0.163 | 3,678,764 |
| 3 | repeat | 17.909 | 106.480 | 0.168 | 5,111,084 |

Mean repeat RTF: **0.1655**, or about **6.04x real-time throughput**.

The 3090 Q8 repeat RTF is about **1.47x faster** than the 1080 Ti Q8 repeat RTF
(0.1655 vs 0.244). Relative to the earlier confirmed Wavhost/PyTorch FP32 3090
repeat mean of 1.559, the observed qwentts.cpp/Q8 path is about **9.4x lower
RTF**. Again, this combines runtime and quantization changes and should not be
reported as a pure Q8 speedup.

The large variation in generated audio duration (62.32 to 106.48 seconds) is a
reminder that this benchmark exercises stochastic generation. RTF is the useful
normalization; wall time alone is misleading. For a cleaner runtime
microcomparison, add a deterministic/greedy benchmark only if both runtimes can
be configured equivalently, but preserve this default-sampling workload because
it reflects actual intended use.

#### RTX 3090 / qwentts.cpp / Q8_0, clamp disabled

After hardening the launch path so one-shot environment overrides preserve the
resolved model mount, the intended Ampere clamp comparison was completed with:

```text
TTS_QWENTTS_CLAMP_FP16=0
NO_FA=0
language=English
physical GPU=0 (RTX 3090)
```

Retained artifact directory:

```text
/data/local-ai/services/tts/benchmarks/20261004T230316Z-qwentts-qwen-0.6-customvoice-q8-ggml-ryan
```

Raw timing data:

| Run | Phase | Wall (s) | Audio (s) | RTF | Bytes |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | first | 3.403 | 21.680 | 0.157 | 1,040,684 |
| 2 | repeat | 3.140 | 21.280 | 0.148 | 1,021,484 |
| 3 | repeat | 2.752 | 18.560 | 0.148 | 890,924 |

Mean repeat RTF: **0.148**, or about **6.76x real-time throughput**.

Compared with the 3090 clamp-enabled repeat mean of 0.1655, this is about
**10.6% lower RTF**. That is useful evidence that the Pascal-oriented clamp is
unnecessary overhead on Ampere, but it is not a tightly controlled kernel
microbenchmark: generation remained stochastic and the clamp-on run generated
much longer utterances. Treat `CLAMP_FP16=0` as the preferred 3090 setting so
far, while keeping the raw rows available for re-analysis.

The structured raw rows for this and the earlier runs now live in
`dev/benchmarks/tts_measurements.csv`; `dev/benchmarks/tts.md` is the compact
summary. Future numeric runs should be appended there rather than growing this
journal with another full table unless the chronology itself is important.

#### Failed second 3090 attempt was a Compose configuration footgun, not a clamp result

An attempted second 3090 run changed `TTS_QWENTTS_CLAMP_FP16` and then manually
recreated the service with raw `docker compose`. qwentts never reached
inference. Its log failed at model open:

```text
[GGUF] Cannot open /models/qwen-talker-0.6b-customvoice-Q8_0.gguf
[Pipeline] failed to load talker GGUF: /models/qwen-talker-0.6b-customvoice-Q8_0.gguf
[Server] FATAL: qt_init: pipeline_tts_load failed ...
```

Root cause: raw Compose invoked from `services/tts` automatically read only that
directory's `.env`; machine-wide `HF_REPOS_ROOT` lives in `../../.env`. The
first successful start had gone through `start-qwentts.sh`, which loaded both.
The manual recreate therefore fell back to the compose default
`/data/services/hf-repos`, while this development machine's existing Q8 files
were under its older configured root. The bind mount pointed at the wrong host
directory and `/models/...` was empty/missing in the container.

**Do not record this as `CLAMP_FP16=0` failing.** No clamp comparison occurred.

Follow-up lifecycle hardening:

- `services/tts/compose.sh` is now the direct Compose entry point. It always
  supplies both `../../.env` and `services/tts/.env` with `--env-file`.
- Caller environment variables retain higher Compose precedence, enabling safe
  one-shot knob overrides without editing config.
- `start-qwentts.sh` preserves explicit `TTS_QWENTTS_*` environment overrides,
  validates boolean/timeout knobs, checks the resolved host model and codec
  files before launch, and prints the effective runtime configuration.
- `benchmark.sh` records selected safe values from the **actual running Docker
  container**, rather than assuming current `.env` still describes the process.

Use this form for future knob experiments:

```bash
TTS_QWENTTS_CLAMP_FP16=0 ./start-qwentts.sh
./scripts/benchmark.sh qwentts 3
```

For raw Compose operations, use:

```bash
./compose.sh stop qwentts
./compose.sh up -d --force-recreate qwentts
```

rather than `docker compose ...` directly.

#### qwentts.cpp knobs currently exposed by local-ai

These are the qwentts experiment controls that existed when the above
measurements were made. Preserve them in benchmark metadata whenever they
change:

| local-ai variable | qwentts container/runtime effect | Baseline value |
| --- | --- | --- |
| `TTS_QWENTTS_GPU` | Docker physical GPU device request | `1` on Ooo; `0` on toothbrush |
| `TTS_QWENTTS_IMAGE` | Runtime image | `ghcr.io/serveurpersocom/qwentts.cpp:cuda12` |
| `TTS_QWENTTS_MODEL_FILE` | `MODEL_PATH` under `/models` | `qwen-talker-0.6b-customvoice-Q8_0.gguf` |
| `TTS_QWENTTS_CODEC_FILE` | `CODEC_PATH` under `/models` | `qwen-tokenizer-12hz-Q8_0.gguf` |
| `TTS_QWENTTS_MODEL_ALIAS` | OpenAI model identity | `qwen-0.6-customvoice-q8-ggml` |
| `TTS_QWENTTS_LANGUAGE` | `TTS_LANG` | `English` |
| `TTS_QWENTTS_CLAMP_FP16` | `CLAMP_FP16` | `1` for initial cross-GPU comparison |
| `TTS_QWENTTS_NO_FA` | `NO_FA` | `0` (flash attention enabled) |
| `TTS_QWENTTS_PORT` | host port | `11436` |
| `TTS_QWENTTS_START_TIMEOUT` | local-ai readiness wait | `180` seconds |

The image digest/ID is also part of a reproducible benchmark even though it is
not a human-tuned knob; `benchmark.sh` records it because the `:cuda12` tag is
mutable.

### Updated performance baseline after Q8_0 experiment

As of the measurements above, avoid duplicating these runs unless a material
runtime/model/kernel/configuration change justifies it:

| Runtime/model | GPU | Repeat RTF | Approx. real-time throughput |
| --- | --- | ---: | ---: |
| Wavhost / PyTorch FP32 | RTX 3090 | 1.559 | 0.64x |
| Wavhost / PyTorch FP32 | GTX 1080 Ti | 3.284 | 0.30x |
| qwentts.cpp / GGUF Q8_0 / clamp=1 / FA on | RTX 3090 | 0.1655 | 6.04x |
| qwentts.cpp / GGUF Q8_0 / clamp=0 / FA on | RTX 3090 | 0.1480 | 6.76x |
| qwentts.cpp / GGUF Q8_0 / clamp=1 / FA on | GTX 1080 Ti | 0.244 | 4.10x |

The 3090 clamp-off comparison is now complete. Q4 is no longer required to meet
a real-time performance threshold; only investigate lower-bit variants if there
is a separate memory/throughput objective and retained audio confirms the quality
tradeoff is acceptable. The next useful measurements should answer a new
question (for example native Wavhost Kokoro versus the existing accelerated
Kokoro service), rather than repeating the established Qwen Q8 baselines.

### Wavhost upstream contribution status after hardware validation

The Wavhost changes developed during this work are tracked upstream as:

```text
https://github.com/smitgol/wavhost/pull/14
[WIP] Improve Qwen CUDA compatibility and server reuse
```

At the time of the final polish pass, that PR covered capability-aware Qwen
dtype selection, warm backend reuse, package-wide server logging, and explicit
noninteractive `wavhost pull --yes` provisioning. Real-hardware validation now
exists for the motivating compatibility case: patched Wavhost generated valid
Qwen3-TTS 0.6B CustomVoice audio on a GTX 1080 Ti in FP32. The qwentts.cpp Q8
work is local-ai experimentation and is **not** part of the Wavhost PR.

Before calling PR #14 merge-ready, the submodule received a deliberately narrow
polish pass rather than another architectural change:

- `CHANGELOG.md` records dtype selection, warm backend caching, package logging,
  and `pull --yes`.
- `--yes` tests assert that automation removes interactive prompts but still
  displays the model license text being accepted.
- logging tests assert package-handler setup is idempotent and does not multiply
  output handlers across entry points.
- README notes that dtype selection cannot compensate for a PyTorch wheel that
  has dropped the target GPU architecture, and records that the Pascal path was
  exercised on a GTX 1080 Ti with a CUDA 12.6 PyTorch build.

Validation after that polish pass:

```text
Wavhost pytest: 193 passed, 4 skipped
```

A standalone Ruff invocation was not available in the artifact-building
container, so do not misrecord Ruff as having been run there. Python compilation,
`git diff --check`, and the full pytest suite passed. The implementation had
already passed the earlier Wavhost test cycles before this documentation/test
polish as well.


### 2026-10-04 — Native Wavhost Kokoro GPU serving and storage-path split-brain

Provisioning `kokoro` correctly wrote `hexgrad/kokoro:latest`, but local-ai's
service manifest initially checked `registry/kokoro/kokoro/latest`. The durable
paths are `manifests/registry/hexgrad/kokoro/latest` and
`checkpoints/hexgrad/kokoro/latest`.

A subsequent `./compose.sh run ... wavhost run kokoro` reported that Kokoro was
not installed even though setup found the manifest. This exposed a second
duplicate authority: `local_ai.py` resolves blank `TTS_DATA_ROOT` from the
manifest as `{LOCAL_AI_SERVICE_ROOT}/tts`, while the Compose file had its own
hard-coded fallback. On a machine whose existing `LOCAL_AI_SERVICE_ROOT` already
ended in `/tts`, setup therefore inspected `/data/services/local-ai/tts/tts`
while raw Compose mounted `/data/services/local-ai/tts`. The files were real;
the container was looking at a different tree. `compose.sh` now delegates to a
generic `local_ai.py compose` command so setup/start/direct Compose operations
share the same resolved environment and manifest defaults. The Compose file now
also requires resolved `TTS_DATA_ROOT` / `HF_REPOS_ROOT` values instead of
falling back silently, so bypassing the wrapper fails early rather than mounting
a plausible wrong tree.

Wavhost's `KokoroBackend` already supports CUDA (`KModel(...).to(device)`), but
the HTTP server previously had no device override and the Kokoro registry entry
recommends CPU. Wavhost now exposes a server-wide `WAVHOST_DEVICE` policy and
`wavhost serve --device`. `auto` preserves per-model recommendations; explicit
`cuda`, `cpu`, or `mps` overrides them. The local-ai GPU TTS container defaults
to `WAVHOST_DEVICE=cuda`, enabling native GPU Kokoro through the same
`/v1/audio/speech` endpoint. Explicit unavailable accelerators fail instead of
silently producing a misleading CPU benchmark.

A dedicated `benchmark.sh wavhost-kokoro` target uses the Wavhost endpoint while
`benchmark.sh kokoro` continues to mean the standalone Remsky Kokoro-FastAPI GPU
service. Future measurements should record both separately in
`dev/benchmarks/tts_measurements.csv`.

Validation for this GPU-Kokoro/device-policy pass: Wavhost `pytest` completed
with **199 passed, 4 skipped**. Shell syntax, Python compilation, TOML parsing,
and `git diff --check` also passed. A fake-Docker exercise of
`local_ai.py compose` using `LOCAL_AI_SERVICE_ROOT=/data/services/local-ai/tts`
resolved `TTS_DATA_ROOT=/data/services/local-ai/tts/tts` and preserved a caller
`TTS_QWENTTS_CLAMP_FP16=0` override, confirming that direct lifecycle operations
now agree with setup while retaining experiment overrides.

### 2026-10-04 — Human audio validation overturns the Pascal qwentts deployment conclusion

The earlier qwentts.cpp Q8 timing results on the GTX 1080 Ti were real, but the
initial benchmark only established throughput and mechanically valid WAV output.
It did **not** establish intelligible speech. A later explicit human-listening
gate showed that distinction matters: qwentts can return structurally valid,
plausibly sized 24 kHz WAVs at excellent RTF while the actual speech is badly
garbled.

This section supersedes the earlier performance-only inference that the Q8 path
was ready to serve the Android reader on Pascal. The performance numbers remain
valid measurements; the deployment conclusion does not.

#### Human listening gate and transport isolation

The Android-side test workflow was extended with an optional listening gate that
requests a fixed phrase from the real server, writes the returned audio to local
disk, plays it, and requires explicit human confirmation. This was added because
HTTP 200, MIME type, WAV/MP3 structure, duration, and file size are insufficient
quality gates for TTS.

The first `Hello world.` samples from the GTX 1080 Ti qwentts path were complete
garbled trash. To isolate transport and transcoding, the same request was then
captured in three forms:

```text
raw qwentts WAV from :11436
gateway WAV from :11437
gateway MP3 from :11437
```

The **raw qwentts WAV was already garbled**, proving the Android app, LAN
transport, qwentts gateway, and WAV->MP3 transcode were not the source of the
quality failure.

Short qwentts utterances were also pathological in duration: `Hello world.`
could produce roughly 30 seconds of junk. A representative paragraph was
therefore adopted as the quality probe for subsequent comparisons:

> Today we are going to talk about how computers represent information. A
> computer ultimately works with patterns of numbers, but those numbers can
> represent text, images, music, and even complex simulations. The important
> idea is that the meaning comes from how we choose to interpret those patterns.

#### Full-precision Wavhost control on GTX 1080 Ti

The known PyTorch/Wavhost path was restored on `Ooo` as a sanity check using:

```text
model:  qwen-0.6-customvoice
voice:  Ryan / Aiden
device: GTX 1080 Ti
WAVHOST_QWEN_DTYPE=float32
```

A two-word `Hello world.` sample was intelligible but sounded noticeably poor.
The longer representative paragraph was much better: **Ryan sounded fine**, and
**Aiden also sounded fine**. This establishes that the official Qwen model,
named voices, 1080 Ti, CUDA/PyTorch stack, LAN path, and WAV framing can all
produce acceptable speech on the same hardware. The poor short-sample behavior
should therefore not be used by itself as a model-quality verdict.

This also refines an earlier subjective note in this journal: Ryan quality is
acceptable on realistic paragraph-sized text, not merely on an unspecified
single sample.

#### Wavhost MP3 packaging defect

While exercising Wavhost as the quality control, MP3 output failed after
successful synthesis:

```text
ImportError: TorchCodec is required for save_with_torchcodec.
RuntimeError: Failed to convert audio to mp3: TorchCodec is required ...
```

The current Wavhost `AudioConverter` avoids TorchCodec for WAV by using its own
WAV encoder, but compressed formats still call `torchaudio.save()`. With the
current TorchAudio build that path requires TorchCodec, which is not installed
in the local-ai Wavhost image. This is a packaging/conversion defect, separate
from Qwen synthesis quality. The image already contains `ffmpeg`; a durable fix
should likely encode compressed formats through ffmpeg rather than adding a
fragile Torch/TorchAudio/TorchCodec dependency coupling. WAV generation remains
usable as a control in the meantime.

#### qwentts Q8 on GTX 1080 Ti: runtime knobs did not recover quality

The Q8/Q8 qwentts configuration on `Ooo` used:

```text
qwen-talker-0.6b-customvoice-Q8_0.gguf
qwen-tokenizer-12hz-Q8_0.gguf
GTX 1080 Ti, compute capability 6.1
```

Several plausible Pascal-specific mitigations were tested and remained garbled:

```text
flash attention off + clamp_fp16 on                 BAD
flash attention off + clamp_fp16 on + greedy/seed  BAD
realistic paragraph instead of Hello world          BAD
```

The deterministic test set `seed=42`, `temperature=0`, and
`subtalker_temperature=0`; it still produced junk. This reduces the likelihood
that the failure is merely stochastic sampling instability.

#### qwentts F32 on GTX 1080 Ti is also garbled

To determine whether the downloaded Q8 artifact or quantization itself was bad,
the qwentts runtime on the GTX 1080 Ti was switched to the published F32 pair:

```text
qwen-talker-0.6b-customvoice-F32.gguf
qwen-tokenizer-12hz-F32.gguf
```

The F32 qwentts output was also garbage. Therefore this is **not adequately
explained by Q8 quantization quality**. A different quantization should not be
assumed to solve the 1080 Ti problem without evidence.

#### Exact Q8 model files work correctly on RTX 3090

The decisive cross-hardware control used the same published Q8 talker and codec
on an RTX 3090 via the same `ghcr.io/serveurpersocom/qwentts.cpp:cuda12`
runtime family. The 3090 log reported:

```text
Device 0: NVIDIA GeForce RTX 3090, compute capability 8.6
qwen-talker-0.6b-customvoice-Q8_0.gguf
qwen-tokenizer-12hz-Q8_0.gguf
fa=on clamp_fp16=off max_batch=1
```

The representative Ryan paragraph produced a 24 kHz mono WAV of 23.76 seconds
and **sounded fine** when listened to directly. This is strong evidence that:

```text
Q8 GGUF files                    good
Q8 quantization                  viable on Ampere
qwentts HTTP/WAV framing         good
Ryan voice                       good
RTX 3090 CUDA execution path     good
GTX 1080 Ti qwentts CUDA path    suspect
```

The same artifacts producing good audio on Ampere and garbage on Pascal is much
stronger evidence than merely comparing quantized versus full precision on one
machine.

#### Separate operational failure: GPU-memory contention

One qwentts process abort on `Ooo` occurred while another Ollama workload was
also occupying GPU 1. The failure occurred after generation with a GGML backend
allocation assertion:

```text
GGML_ASSERT(... ggml_backend_buffer_get_alloc_size ... <= ... buffer_size) failed
...
Aborted (core dumped)
```

After taking the conflicting Ollama workload off GPU 1, a five-run Android
server preflight through the qwentts MP3 gateway passed 5/5. Treat this as a
separate residency/VRAM-headroom issue. It explains that crash, but it does not
explain the consistently garbled raw WAVs on an otherwise free 1080 Ti.

The qwentts gateway now retries transient synthesis failures and waits for the
engine to become healthy after a container restart before spending the next
retry. That recovery behavior is useful operationally, but it cannot correct
numerically wrong audio returned successfully by the engine.

#### Current diagnosis and pending Pascal controls

The current best diagnosis is a **qwentts.cpp / GGML CUDA correctness problem on
Pascal**, not an Android, MP3, LAN, model-download, voice, or generic Qwen issue.
Upstream's CUDA-12 image explicitly claims Pascal `sm_61` support, so if the
remaining controls fail this is a legitimate upstream correctness bug rather
than simply unsupported hardware.

Before filing that conclusion, run these remaining controls:

1. Compare the exact Docker image ID / repo digest on `Ooo` and `toothbrush`.
   The `:cuda12` tag is mutable; different image builds would invalidate the
   otherwise-clean cross-machine comparison.
2. Run the 1080 Ti with the exact known-good 3090 runtime knobs:
   `fa=on`, `clamp_fp16=off`.
3. Run a standalone qwentts CUDA-12 container on the 1080 Ti with
   `GGML_CUDA_DISABLE_GRAPHS=1`, bypassing local-ai lifecycle code and disabling
   ggml CUDA graph capture/replay.
4. If needed, run the qwentts `:cpu` image on `Ooo` with the same GGUFs. Good
   CPU output plus bad CUDA output on the same host would isolate the failure to
   the CUDA backend even more directly.

If the image digest matches and the no-CUDA-graphs test is still bad, preserve
all logs and a representative bad WAV for an upstream qwentts.cpp report.

### Revised deployment status after human quality validation

The current operational choices are now:

| Host/runtime | Speed | Human quality | Status |
| --- | --- | --- | --- |
| RTX 3090 + qwentts.cpp Q8 | much faster than real time | good | viable |
| GTX 1080 Ti + qwentts.cpp Q8 | much faster than real time | garbage | **not viable** |
| GTX 1080 Ti + qwentts.cpp F32 | not yet the performance target | garbage | **not viable** |
| GTX 1080 Ti + Wavhost/PyTorch FP32 | ~3.28 RTF baseline | good on paragraph-sized text | slow fallback/control |

Do not use the earlier GTX 1080 Ti Q8 throughput numbers as evidence that the
backend is deployable. They remain useful performance measurements for a broken
quality path. Any future TTS benchmark intended to support a deployment decision
must pair mechanical validity with an explicit retained human-listenable sample.

### 2026-10-04 — Controlled Pascal matrix isolates `CLAMP_FP16` as the qwentts corruption trigger

The provisional diagnosis above that the qwentts.cpp CUDA path was generally
incorrect on Pascal was overturned by a controlled follow-up. The same qwentts
CUDA-12 image, same Q8 GGUF pair, and same GTX 1080 Ti can produce clean,
intelligible speech. The reproducible failure is specifically associated with
`CLAMP_FP16=1`.

First, the floating `:cuda12` image tag was ruled out as a source of
cross-machine drift. `Ooo` (GTX 1080 Ti) and `toothbrush` (RTX 3090) were both
running the exact same image:

```text
image id:
sha256:52f7faf81063137a500cf3f92d6da01306a5cb22e2deaf3562b88eaacfc3d8fb

repo digest:
ghcr.io/serveurpersocom/qwentts.cpp@sha256:01cfb156657b009c5514d83988f8c633faa78b99dc72e1a6f8df1f5c042fbc2a
```

The exact same Q8 files had already produced good Ryan audio on the RTX 3090:

```text
qwen-talker-0.6b-customvoice-Q8_0.gguf
qwen-tokenizer-12hz-Q8_0.gguf
```

A standalone GTX 1080 Ti run using `fa=on`, `clamp_fp16=off`, and an explicit
`GGML_CUDA_DISABLE_GRAPHS=1` also produced correct speech. Its log reported:

```text
Device 0: NVIDIA GeForce GTX 1080 Ti, compute capability 6.1
[GGML] ggml_cuda_graph_set_enabled: disabling CUDA graphs due to GPU architecture
[Pipeline] Loaded: ... fa=on clamp_fp16=off ...
[Perf] Total 5379.2 ms ... audio 22.56 s, RTF 0.238
```

The same configuration then worked through the normal local-ai lifecycle, so
the standalone container was not bypassing a local-ai correctness problem.
Because ggml automatically disables CUDA graphs on this GPU architecture,
`GGML_CUDA_DISABLE_GRAPHS=1` was not the meaningful difference.

#### Clean 2x2 `NO_FA` / `CLAMP_FP16` matrix

The two runtime knobs were then tested as a complete 2x2 matrix on `Ooo`. Each
case used a fresh qwentts container, the same Q8 talker and codec, the same Ryan
paragraph, and fixed seeds 42 and 1234. The results were listened to directly as
raw WAVs so the Android app, gateway, MP3 transcode, and network transport were
not part of the judgment.

| Case | Flash attention | `CLAMP_FP16` | Seed 42 | Seed 1234 |
| --- | --- | --- | --- | --- |
| A | on | off | good | good |
| B | on | on | bad / garbled | bad / garbled |
| C | off | off | good | good |
| D | off | on | effectively empty / silent | bad / garbled |

This matrix is the key result. Disabling flash attention by itself does **not**
corrupt output. Enabling `CLAMP_FP16` corrupts output with flash attention either
on or off. Therefore the best-supported diagnosis is:

```text
CLAMP_FP16=0  -> correct audio on GTX 1080 Ti
CLAMP_FP16=1  -> incorrect audio on GTX 1080 Ti
NO_FA alone   -> not a correctness problem in this test
```

The `D` / seed-42 failure was repeated once more because it initially looked
like a zero-duration artifact. It reproduced. The server did begin generation,
but sampled EOS almost immediately:

```text
[Prompt] Built: 62 ids, N_text=54, N_instruct=0, T_ctx=65, ...
[Sample] step=0 c0=1995 ...
[Sample] step=1 c0=1100 ...
[Pipeline] EOS at step 5, stopping (slot 0)
[Pipeline] Generation done : 5 frames
[Perf] Total 127.5 ms (5 frames, 19.03 ms/frame AR, audio 0.40 s, RTF 0.319)
```

So this case is not merely a codec producing silence from an otherwise normal
sequence. With the clamp enabled, autoregressive generation itself is perturbed
enough to terminate after only five frames. That is stronger evidence of a
model-execution correctness problem than a downstream WAV/codec problem.

A follow-up run of the same FA-off / clamp-on case with seed 43 produced
garbled speech rather than the seed-42 early EOS. This reinforces that seed 42
is a particular failure manifestation (likely sampling EOS from corrupted
logits), while the underlying correctness failure is clamp-dependent rather
than seed-dependent.

#### Corrections to earlier interpretations

Several earlier conclusions must be narrowed in light of this matrix:

1. **qwentts.cpp is not generally broken on Pascal.** Q8/Q8 produces good audio
   on a GTX 1080 Ti when `CLAMP_FP16=0`.
2. **The Q8 artifacts are not bad.** The same files work on both RTX 3090 and
   GTX 1080 Ti under a correct runtime configuration.
3. **Flash attention is not required for correctness.** Both FA-on and FA-off
   cases were good with clamping disabled.
4. **The previous qwentts F32/Pascal failure does not establish an F32 bug.**
   That experiment was performed with the now-known-bad clamp-enabled
   configuration. There is no reason to repeat F32 unless a separate F32
   question becomes important.
5. **CUDA graphs are not the culprit.** ggml already disables them automatically
   on the GTX 1080 Ti architecture.
6. The earlier Q8 benchmark at roughly 0.244 repeat RTF remains a useful
   throughput observation, but its clamp-enabled audio was not deployment
   quality. A later correct-audio standalone run measured RTF 0.238 for a
   22.56-second sample, demonstrating that the desired faster-than-real-time
   performance survives with the correct runtime settings.

#### Operational setting and upstream significance

For the GTX 1080 Ti deployment, the validated qwentts settings are now:

```text
TTS_QWENTTS_NO_FA=0
TTS_QWENTTS_CLAMP_FP16=0
```

`NO_FA=1` also produced correct audio with clamping disabled, but there is no
current reason to disable the normal flash-attention path. The production
preference is therefore FA on, clamp off.

This is a useful upstream correctness finding because `CLAMP_FP16` is intended
as a guard for FP16 numerical range on older/sub-Ampere CUDA hardware, yet on
this Pascal card enabling it reproducibly makes generation wrong. The failure
is not merely lower subjective quality: it can cause severe garbling and, for a
fixed seed, premature EOS after five frames. If reported upstream, include the
2x2 matrix, exact image digest, model filenames, GPU model/compute capability,
and the five-frame premature-EOS log above.

The qwentts Q8 path is therefore back to being the preferred fast backend for
`Ooo`, provided `CLAMP_FP16` remains disabled and human-listenable samples are
kept as part of future deployment validation.

### 2026-10-04 — Q8 1.7B CustomVoice fits Pascal and costs only ~15% RTF versus 0.6B

After the clamp-off Pascal configuration was established as correct, the
quantized qwentts deployment was extended to the larger Qwen3-TTS 1.7B
CustomVoice talker. The purpose of this experiment was to answer two practical
questions for the Android reader deployment:

1. Does the Q8 1.7B CustomVoice model fit and run correctly on an 11 GB GTX
   1080 Ti?
2. If it does, how much throughput does it give up relative to the already
   validated 0.6B Q8 deployment?

The 1.7B Q8 model loaded and generated correctly on `Ooo` using the same shared
Q8 codec and the validated Pascal runtime settings:

```text
qwen-talker-1.7b-customvoice-Q8_0.gguf
qwen-tokenizer-12hz-Q8_0.gguf
TTS_QWENTTS_NO_FA=0
TTS_QWENTTS_CLAMP_FP16=0
```

This establishes that the quantized 1.7B CustomVoice model is operationally
compatible with the GTX 1080 Ti deployment. No out-of-memory failure occurred
in the tested reader-sized workload.

#### Controlled remote 0.6B versus 1.7B comparison

The comparison was run from `toothbrush` against the raw qwentts endpoint on
`Ooo` (`:11436`), so it includes ordinary LAN request/response overhead but
excludes the MP3 gateway/transcode. Both models used the same benchmark helper,
Ryan voice, English language, the same lecture paragraph, fixed seed, one
unmeasured warmup request, and five measured requests. The warmup was excluded
from the summary.

The generated audio duration was deterministic within each model but differed
between models despite the otherwise matching request. Therefore RTF is the
primary throughput comparison rather than wall time alone.

Raw 1.7B measurements:

| Run | Wall (s) | Audio (s) | RTF |
| ---: | ---: | ---: | ---: |
| 1 | 6.134 | 22.240 | 0.2758 |
| 2 | 6.122 | 22.240 | 0.2753 |
| 3 | 6.124 | 22.240 | 0.2754 |
| 4 | 6.109 | 22.240 | 0.2747 |
| 5 | 6.154 | 22.240 | 0.2767 |

1.7B summary:

```text
mean wall:    6.129 s
median wall:  6.124 s
mean audio:  22.240 s
mean RTF:     0.2756
median RTF:   0.2753
throughput:   3.63x realtime
```

Raw 0.6B measurements from the same harness:

| Run | Wall (s) | Audio (s) | RTF |
| ---: | ---: | ---: | ---: |
| 1 | 4.802 | 20.080 | 0.2391 |
| 2 | 4.948 | 20.080 | 0.2464 |
| 3 | 4.767 | 20.080 | 0.2374 |
| 4 | 4.777 | 20.080 | 0.2379 |
| 5 | 4.781 | 20.080 | 0.2381 |

0.6B summary:

```text
mean wall:    4.815 s
median wall:  4.781 s
mean audio:  20.080 s
mean RTF:     0.2398
median RTF:   0.2381
throughput:   4.17x realtime
```

Direct normalized comparison:

```text
0.6B mean RTF:          0.2398
1.7B mean RTF:          0.2756
1.7B / 0.6B RTF ratio:  1.15x
1.7B RTF slowdown:      14.9%
```

The mean wall time increased from 4.815 s to 6.129 s, about 27%, but that is not
the right standalone performance conclusion because the 1.7B request generated
22.24 seconds of audio versus 20.08 seconds for 0.6B. Normalizing by generated
audio duration shows the larger model is only about 14.9% slower in this
workload.

#### Deployment interpretation

The larger Q8 model remains comfortably faster than real time on the GTX 1080
Ti: approximately 3.63x realtime versus 4.17x for 0.6B. That leaves substantial
prefetch margin for the long-form Android reader. The 1.7B model is therefore a
practical deployment option rather than merely a model that happens to fit in
VRAM.

Do not infer a quality win from this timing experiment alone. Ryan/Aiden voice
quality and preference should be judged separately with retained listen-gate
samples. From a throughput and compatibility perspective, however, the 1.7B
Q8 model is viable enough that model choice can be based primarily on audible
quality rather than GTX 1080 Ti performance constraints.

### 2026-10-07 — Candidate TTS matrix for Pascal-first deployment

The next candidate serving pass adds IndexTTS 2.5, Chatterbox Flash, and
Chatterbox Nano. The primary success criterion is still the GTX 1080 Ti lecture
server; RTX 3090 runs are comparison/render measurements.

A key lesson from the qwentts clamp incident is now encoded in the harness:
HTTP 200 + low RTF + structurally valid WAV is not enough. Candidate benchmarks
retain every sample, run mechanical duration/RMS/finite-value checks, record the
exact CUDA architecture exposed by PyTorch, and emit a mandatory human listening
checklist. Promotion requires the listening gate to pass on Pascal.

For cross-hardware comparability the candidate images intentionally standardize
on PyTorch 2.7.1 / CUDA 12.6. This wheel line provides a binary-compatible
Pascal cubin (`sm_60` on the tested CC 6.1 card) and native Ampere `sm_86`. The
runtime endpoint reports `torch.cuda.get_arch_list()`, actual
compute capability, dtype policy, exact source/model revisions, and CUDA memory
allocation. The benchmark fails before synthesis if the actual SM is absent
from the compiled architecture list.

Pinned experiments:

```text
IndexTTS 2.5 source:
  6e353fe2611afb6a7e3f13a7a6be3d4e27142119
IndexTTS 2.5 model:
  c39ce5ba981572cb187443877ff559dfb246ce63
IndexTTS auxiliary checkpoints:
  facebook/w2v-bert-2.0                 da985ba0987f70aaeb84a80f2851cfac8c697a7b
  amphion/MaskGCT                       265c6cef07625665d0c28d2faafb1415562379dc
  funasr/campplus                        e4b6ede7ce16997aff4ae69fbca1f0175e2afede
  nvidia/bigvgan_v2_22khz_80band_256x  633ff708ed5b74903e86ff1298cf4a98e921c513

Chatterbox source (Nano):
  5de7a54aa4e5e2baadb0182dde554908b48b85c2
Chatterbox Nano model:
  71ccd1d0081b430592cea481f4307e764e07bc64

Chatterbox Flash source:
  74e05baa8ce574bf2cc571702391a21f1b0d48c5
Chatterbox Flash model:
  4385507288b8197e6dab8b4e6b1603328d549d9d
Perth source:
  ce86c49d029f42272c1902eccb675556b9ed2330
```

Baseline policy is correctness-first: IndexTTS disables optional fused/custom
acceleration and uses FP32 on Pascal / BF16 on Ampere; Flash uses the portable
Torch attention engine with CUDA graphs off. Optimization is a follow-up only
after retained audio passes on both GPUs.

No performance numbers are recorded here yet: this overlay creates the
reproducible experiments. Run `./scripts/run-candidate-matrix.sh 3 1` on `Ooo`
and the same command with GPU `0` on the 3090 comparison host, then append the
measured rows after listening review.


### 2026-10-07: candidate matrix CUDA architecture proof correction

The first GTX 1080 Ti Chatterbox Nano run exposed a false-negative in the
candidate benchmark gate.  The runtime successfully loaded Nano on a CC 6.1
GTX 1080 Ti with PyTorch 2.7.1+cu126 and reported compiled CUDA arches
`sm_50, sm_60, sm_70, sm_75, sm_80, sm_86, sm_90`, but the harness required an
exact literal `sm_61` entry.  That is stricter than CUDA's cubin compatibility
contract: within a compute-capability major, a cubin targeting minor `m` may run
on a GPU with the same major and minor >= `m`.  Therefore `sm_60` is a valid
binary target for CC 6.1.

The proof now selects the highest binary-compatible cubin with matching major
and compiled minor <= device minor, while still failing if no compatible cubin
exists.  This preserves the purpose of the proof instead of simply disabling it.
The interrupted Nano run had already established useful pre-benchmark facts:
model load succeeded, effective dtype was float32, and CUDA allocation was about
2.1 GiB on the GTX 1080 Ti.  Audio correctness/RTF remained unmeasured because
the false architecture gate stopped before synthesis.

### 2026-10-07 — Candidate matrix results on GTX 1080 Ti and RTX 3090

The candidate matrix was completed on both target hardware tiers using the
pinned experiment stack described above. The primary deployment target remains
`Ooo` / GTX 1080 Ti; the RTX 3090 run on `toothbrush` is a comparison/render
measurement rather than a requirement for interactive lecture playback.

Common experiment properties:

```text
PyTorch:       2.7.1+cu126
CUDA runtime:  12.6
reference:     retained Ryan WAV
runs:          3 per backend (1 first + 2 repeat)
1080 Ti GPU:   physical GPU 1, compute capability 6.1, compatible cubin sm_60
3090 GPU:      physical GPU 0, compute capability 8.6, native cubin sm_86
```

The dtype policy was intentionally architecture-sensitive rather than forced to
one precision everywhere:

```text
                     GTX 1080 Ti       RTX 3090
Chatterbox Nano      float32           float32
Chatterbox Flash     float32           bfloat16
IndexTTS 2.5         float32           bfloat16
```

Therefore the Flash and IndexTTS cross-GPU ratios below combine GPU generation
and dtype policy. Nano is the cleaner FP32-to-FP32 hardware comparison.

#### GTX 1080 Ti results

All three candidates loaded and synthesized successfully on Pascal. Mechanical
WAV checks passed, and direct listening found all three outputs coherent and
usable. The listener could not identify a large quality gap in this initial
lecture-like prompt. IndexTTS 2.5 may have sounded slightly better, but the
preference was weak/hard to distinguish.

| Backend | Startup evidence | First RTF | Repeat RTFs | Repeat mean RTF | Throughput | Audio duration | Runtime CUDA allocation |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| Chatterbox Nano | 46.556 s cold-load evidence | 0.3799 | 0.3490, 0.3475 | **0.3483** | **2.87x realtime** | 18.880 s | 2,104,182,272 B (~1.96 GiB) |
| Chatterbox Flash | 58.458 s | 0.7383 | 0.7345, 0.7339 | **0.7342** | **1.36x realtime** | 31.840 s | 3,510,799,360 B (~3.27 GiB) |
| IndexTTS 2.5 | 104.091 s | 1.6387 | 1.3271, 1.3061 | **1.3166** | **0.76x realtime** | 16.893 s | 6,952,131,584 B (~6.47 GiB) |

Nano's post-hotfix matrix rerun printed `startup_seconds=0.750` because its
container was already running from the interrupted architecture-proof attempt.
That is a warm restart and must not be compared with the cold Flash/Index
startup measurements. The earlier interrupted Nano run measured 46.556 s from
container start to readiness and is the appropriate cold-load evidence.

Retained Pascal artifacts:

```text
/data/services/local-ai/tts/tts/benchmarks/20261007T193206Z-chatterbox-nano-chatterbox-nano-ryan
/data/services/local-ai/tts/tts/benchmarks/20261007T193336Z-chatterbox-flash-chatterbox-flash-ryan
/data/services/local-ai/tts/tts/benchmarks/20261007T193642Z-indextts25-indextts-2.5-ryan
```

#### RTX 3090 results

The same pinned matrix was run on physical GPU 0 of `toothbrush`.

| Backend | Startup | First RTF | Repeat RTFs | Repeat mean RTF | Throughput | Audio duration |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| Chatterbox Nano | 21.696 s | 0.1525 | 0.1455, 0.1385 | **0.1420** | **7.04x realtime** | 18.880 s |
| Chatterbox Flash | 24.641 s | 0.2851 | 0.2694, 0.2690 | **0.2692** | **3.71x realtime** | 16.640 s |
| IndexTTS 2.5 | 39.748 s | 0.7120 | 0.5589, 0.5700 | **0.5645** | **1.77x realtime** | 18.611 s |

Retained Ampere artifacts:

```text
/data/local-ai/services/tts/benchmarks/20261007T205337Z-chatterbox-nano-chatterbox-nano-ryan
/data/local-ai/services/tts/benchmarks/20261007T205421Z-chatterbox-flash-chatterbox-flash-ryan
/data/local-ai/services/tts/benchmarks/20261007T205526Z-indextts25-indextts-2.5-ryan
```

The pasted 3090 summary did not include the `/v1/runtime` memory fields, so do
not invent or back-fill 3090 VRAM usage from model size. The full retained log
can supply those later if needed.

#### Cross-GPU normalized comparison

Using only the two repeat RTF measurements on each GPU:

| Backend | 1080 Ti repeat RTF | 3090 repeat RTF | 3090 RTF improvement |
| --- | ---: | ---: | ---: |
| Chatterbox Nano | 0.3483 | 0.1420 | **2.45x lower RTF** |
| Chatterbox Flash | 0.7342 | 0.2692 | **2.73x lower RTF** |
| IndexTTS 2.5 | 1.3166 | 0.5645 | **2.33x lower RTF** |

Do not interpret the Flash/Index ratios as pure hardware acceleration because
those candidates also switch FP32 -> BF16 on Ampere. Nano remains FP32 on both
machines and is therefore the most direct hardware-only comparison of the
three.

Generated duration also changed materially for the stochastic candidates. Nano
produced exactly 18.880 s on both machines, but Flash produced 31.840 s on the
1080 Ti and 16.640 s on the 3090, while IndexTTS produced 16.893 s and 18.611 s
respectively. This is why RTF, not wall time alone, remains the primary
throughput metric. It is also evidence that a single sample should not be used
to make fine-grained prosody/quality claims.

#### Deployment interpretation and listening result

The important Pascal result is that **all three new candidates are genuinely
usable**, not merely executable. This is a substantial contrast with the earlier
qwentts clamp-on failure mode, where mechanically valid and fast WAVs were
corrupted.

For the on-demand lecture role:

- **Chatterbox Nano** is the clear efficiency leader on the GTX 1080 Ti: ~0.348
  steady-state RTF (~2.87x realtime) and ~1.96 GiB active CUDA allocation.
- **Chatterbox Flash** remains comfortably faster than realtime at ~0.734 RTF
  and, after listening across the candidate outputs, was judged **a bit better
  than Nano**. This gives Flash a meaningful quality/latency tradeoff rather
  than making it redundant with Nano.
- **IndexTTS 2.5** was coherent and may be slightly higher quality, but the
  initial preference was weak. In the conservative Pascal baseline it is slower
  than realtime (~1.317 repeat RTF), so it is less attractive for interactive
  chunk generation unless optimization or prefetching closes that gap.

The current practical ranking is therefore not a single scalar winner:

```text
minimum latency / VRAM:     Chatterbox Nano
better audible quality:     Chatterbox Flash (modest preference over Nano)
possible quality ceiling:   IndexTTS 2.5, but not clearly enough better yet to
                            justify its Pascal latency in this simple test
```

Before making a permanent backend choice, run a harder lecture-oriented listen
set (long paragraphs, technical acronyms, math, numbers, punctuation, names,
and 60-90 s expected speech). The current evidence is already sufficient to
keep Nano and Flash as serious Pascal deployment options; future work should
focus on quality discrimination and long-form stability rather than basic GPU
compatibility.

### 2026-10-07: choose Chatterbox Flash for the interactive server

After the cross-GPU comparison and direct listening, the deployment preference
was narrowed from Nano/Flash to **Chatterbox Flash** for the primary interactive
lecture endpoint. Nano remains the lower-latency fallback, but Flash's modest
subjective quality advantage was considered worth its still-realtime Pascal
cost (~0.734 repeat RTF on the GTX 1080 Ti).

The Android reader should remain backend-agnostic. The candidate adapter now
advertises its single retained `ryan` voice directly from `/v1/models` using
`speakers=["ryan"]` and `default_voice="ryan"`, and also implements both
`/v1/audio/voices` and `/v1/voices`. This matches the reader's existing generic
discovery flow; no Chatterbox-specific Android code is required.
