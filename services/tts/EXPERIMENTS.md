# Candidate TTS experiment matrix

The primary deployment target is the GTX 1080 Ti (`Ooo`). RTX 3090 results are
comparison/render data, not a substitute for Pascal validation.

This recipe adds three opt-in candidates:

| backend | default port | model bundle | source pin | model pin |
| --- | ---: | --- | --- | --- |
| `indextts25` | 11438 | `indextts-2.5` | `6e353fe2611afb6a7e3f13a7a6be3d4e27142119` | `c39ce5ba981572cb187443877ff559dfb246ce63` |
| `chatterbox-flash` | 11439 | `chatterbox-flash` | `74e05baa8ce574bf2cc571702391a21f1b0d48c5` | `4385507288b8197e6dab8b4e6b1603328d549d9d` |
| `chatterbox-nano` | 11440 | `chatterbox-nano` | `5de7a54aa4e5e2baadb0182dde554908b48b85c2` | `71ccd1d0081b430592cea481f4307e764e07bc64` |

The Chatterbox image also pins Perth at
`ce86c49d029f42272c1902eccb675556b9ed2330`.

## Why one CUDA stack for both GPUs

All three experiments use PyTorch 2.7.1 + CUDA 12.6 instead of allowing each
upstream project to choose a different wheel. This wheel line retains Pascal
`sm_61` while also supporting Ampere `sm_86`, so GTX 1080 Ti and RTX 3090 runs
exercise the same PyTorch/CUDA version.

The server exposes `/v1/runtime`. The benchmark refuses to count a candidate run
unless the reported `torch.cuda.get_arch_list()` contains the actual device's
compute capability. This catches the common failure mode where a container has
CUDA but its PyTorch wheel silently dropped Pascal kernels.

Correctness comes before accelerator-specific optimization:

- IndexTTS 2.5 starts with BF16 only on Ampere+, FP32 on Pascal, and disables its
  optional custom BigVGAN CUDA kernel, acceleration engine, and torch.compile.
- Chatterbox Flash starts with `backend=torch` and CUDA graphs disabled.
- Chatterbox Nano uses the pinned upstream implementation and the common Ryan
  reference voice.

After a model passes both GPUs, optimized variants can be added as separate
experiments rather than changing the baseline underneath recorded measurements.

## Provision once

From `services/tts`:

```bash
./setup.sh --accept-defaults \
    --with-model indextts-2.5 \
    --with-model chatterbox-flash \
    --with-model chatterbox-nano
```

This is intentionally opt-in. A normal qwentts/Kokoro setup does not download
these checkpoints or build their Python images.

IndexTTS provisioning also fetches the auxiliary checkpoints required by the
pinned upstream implementation (Wav2Vec2-BERT, MaskGCT semantic codec,
CAMPPlus, and BigVGAN) into the model's retained `hf_cache/` tree. Those are
pinned too, rather than following moving `main` branches:

```text
facebook/w2v-bert-2.0                 da985ba0987f70aaeb84a80f2851cfac8c697a7b
amphion/MaskGCT                       265c6cef07625665d0c28d2faafb1415562379dc
funasr/campplus                        e4b6ede7ce16997aff4ae69fbca1f0175e2afede
nvidia/bigvgan_v2_22khz_80band_256x  633ff708ed5b74903e86ff1298cf4a98e921c513
```

Revision-pinned model directories carry `.local-ai-revision`; setup treats a
directory with the right filenames but a mismatched revision marker as
incomplete and reprovisions it. IndexTTS also retains
`.local-ai-aux-revisions.json` beside the model.

## Cross-hardware proof runs

The same command runs the three candidates serially, stopping each before the
next one starts.

On `Ooo`, where the 1080 Ti is physical GPU 1:

```bash
./scripts/run-candidate-matrix.sh 3 1
```

On the 3090 comparison host, when the target card is physical GPU 0:

```bash
./scripts/run-candidate-matrix.sh 3 0
```

A single backend can be repeated with:

```bash
./start-chatterbox-nano.sh
./scripts/benchmark.sh chatterbox-nano 5
```

Equivalent direct starts exist for `chatterbox-flash` and `indextts25`.

## What counts as proof

Each retained benchmark directory contains:

- `host-hardware.txt`: hostname, kernel, physical GPU inventory and driver;
- `runtime-before.json`: exact runtime/model pins, PyTorch/CUDA versions,
  compiled CUDA architecture list, actual GPU and compute capability, dtype;
- `request.json` and `input.txt`;
- every generated WAV;
- per-WAV `*.sanity.json` with duration, RMS, peak and finite-value checks;
- `results.tsv` with wall time, audio duration and RTF;
- `runtime-after.json`, including peak PyTorch CUDA allocation;
- `LISTEN_REVIEW.md`, the mandatory subjective correctness gate.

Mechanical validation is deliberately not called a quality proof. The qwentts
Pascal incident demonstrated that a server can be healthy, fast and return a
valid WAV while the spoken content is wrong. A candidate is deployable on the
1080 Ti only after every retained sample passes the listening checklist.

## Promotion criteria for the lecture reader

The primary target is `Ooo`, so promotion should require:

1. valid `sm_61` runtime proof;
2. all mechanical WAV checks pass;
3. human listening review passes for every retained sample;
4. repeat RTF comfortably below 1.0 (preferably below 0.5 for prefetch margin);
5. startup and VRAM behavior are acceptable for on-demand use;
6. Android can use the OpenAI-compatible endpoint with a saved profile.

The 3090 result is useful to distinguish hardware-specific failures and to
measure a render-quality tier, but it cannot waive a failed Pascal gate.
