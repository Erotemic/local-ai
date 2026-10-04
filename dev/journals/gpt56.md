# GPT-5.6 engineering journal

This file is a durable engineering journal for experiments, failed approaches,
measurements, and tentative conclusions produced while working on `local-ai`.
It is intentionally more verbose than user-facing documentation. The goal is to
preserve enough raw evidence that a future maintainer can reconstruct why a
choice was made without repeating expensive downloads, builds, or GPU runs.

Measurements here are observations, not promises. When a run was not controlled
tightly enough to support a conclusion, that ambiguity is recorded explicitly.

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
