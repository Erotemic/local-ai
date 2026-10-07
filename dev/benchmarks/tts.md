# Local TTS benchmark ledger

This file is the compact human-readable summary of local TTS performance
measurements. The authoritative raw per-run records are in
[`tts_measurements.csv`](tts_measurements.csv). Narrative context, failed paths,
and the reasoning that led to each experiment remain in
[`../journals/gpt56.md`](../journals/gpt56.md).

## Recording policy

- Keep one CSV row per benchmark run; do not replace raw runs with averages.
- Use `use_for_summary=no` for observations that are useful historically but
  cannot support a controlled comparison.
- Preserve `first` versus `repeat`; do not relabel `first` as `cold` unless the
  model/server cache was actually reset by the benchmark procedure.
- Record runtime and quantization separately. A comparison between
  Wavhost/PyTorch FP32 and qwentts.cpp/GGML Q8_0 is an end-to-end serving-path
  comparison, not a pure quantization speedup.
- RTF (`wall_seconds / audio_seconds`) is the primary throughput metric because
  Qwen generation is stochastic and output duration varies substantially.
- Retain benchmark artifact directories when known so the WAVs, request, exact
  metadata, and timing TSV remain inspectable.

## Canonical repeat-run summary

| Runtime / model | GPU | Important knobs | Repeat RTF | Approx. throughput | Status |
| --- | --- | --- | ---: | ---: | --- |
| Wavhost / PyTorch Qwen3-TTS 0.6B | RTX 3090 | FP32, manual attention | 1.559 | 0.64x real time | canonical full-precision baseline |
| Wavhost / PyTorch Qwen3-TTS 0.6B | GTX 1080 Ti | FP32, manual attention | 3.284 | 0.30x real time | canonical Pascal full-precision baseline |
| qwentts.cpp / GGML Q8_0 | RTX 3090 | clamp=1, FA on | 0.1655 | 6.04x real time | cross-GPU comparable Q8 baseline |
| qwentts.cpp / GGML Q8_0 | RTX 3090 | clamp=0, FA on | 0.1480 | 6.76x real time | preferred Ampere configuration so far |
| qwentts.cpp / GGML Q8_0 | GTX 1080 Ti | clamp=0, FA on | ~0.238 | ~4.20x real time | later correctness-validated Pascal configuration; standalone retained sample |

## Current conclusions

1. The qwentts.cpp/GGML Q8_0 serving path is comfortably faster than real time
   on both tested GPUs. The later Pascal correctness matrix established that
   `CLAMP_FP16=0` is required on the tested GTX 1080 Ti; `CLAMP_FP16=1` can
   produce garbling or premature EOS even though the returned WAV is structurally
   valid.
2. The older 1080 Ti clamp-enabled rows remain in the CSV as historical timing
   observations, but they must not be used as a deployment-quality baseline.
   The journal records a later correct-audio clamp-off sample at about 0.238 RTF.
3. On the RTX 3090, disabling the clamp reduced the observed repeat mean RTF
   from 0.1655 to 0.1480. Treat this as a runtime-knob observation rather than a
   precision-only benchmark because generated durations differed.
4. The end-to-end qwentts/Q8 improvement over the old Wavhost/PyTorch path is
   large, but runtime, kernels, quantization and serving architecture all changed.
5. New candidate experiments must retain audio and pass a human listening gate.
   HTTP success, low RTF, and mechanical WAV sanity are explicitly insufficient
   evidence after the Pascal clamp failure.

## Candidate matrix: 2026-10-07

All three candidates produced coherent, usable speech on the GTX 1080 Ti. The
initial listening pass found no large quality gap; IndexTTS 2.5 may be slightly
better, and a later comparison judged Chatterbox Flash a bit better than Nano.
These are subjective impressions from a small retained sample set, not formal
quality scores.

| Runtime / model | GPU | Dtype | Repeat RTF | Approx. throughput | Listening / role |
| --- | --- | --- | ---: | ---: | --- |
| Chatterbox Nano | GTX 1080 Ti | FP32 | **0.3483** | **2.87x realtime** | coherent; fastest / lowest-VRAM candidate |
| Chatterbox Flash | GTX 1080 Ti | FP32 | **0.7342** | **1.36x realtime** | coherent; modest audible preference over Nano |
| IndexTTS 2.5 | GTX 1080 Ti | FP32 | **1.3166** | **0.76x realtime** | coherent; possibly slightly higher quality, but slower than realtime |
| Chatterbox Nano | RTX 3090 | FP32 | **0.1420** | **7.04x realtime** | comparison/render tier |
| Chatterbox Flash | RTX 3090 | BF16 | **0.2692** | **3.71x realtime** | comparison/render tier |
| IndexTTS 2.5 | RTX 3090 | BF16 | **0.5645** | **1.77x realtime** | comparison/render tier |

The 3090/1080 Ti repeat-RTF ratios are approximately 2.45x (Nano), 2.73x
(Flash), and 2.33x (IndexTTS). Only Nano is a clean FP32-to-FP32 hardware
comparison; Flash and IndexTTS also change to BF16 on Ampere.

On the 1080 Ti the runtime endpoint reported active CUDA allocation of about
1.96 GiB for Nano, 3.27 GiB for Flash, and 6.47 GiB for IndexTTS 2.5. The pasted
3090 summary did not include memory metadata, so no 3090 VRAM number is recorded
here.

## Known non-canonical observation

The CSV preserves one 3090 Wavhost run whose dtype chronology was ambiguous.
Those rows have `use_for_summary=no` and `confidence=low`. They are retained so
future archeology does not rediscover the numbers and mistake them for a missing
experiment.
