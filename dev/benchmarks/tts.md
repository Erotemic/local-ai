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

## Known non-canonical observation

The CSV preserves one 3090 Wavhost run whose dtype chronology was ambiguous.
Those rows have `use_for_summary=no` and `confidence=low`. They are retained so
future archeology does not rediscover the numbers and mistake them for a missing
experiment.
