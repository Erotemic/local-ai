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
| qwentts.cpp / GGML Q8_0 | GTX 1080 Ti | clamp=1, FA on | 0.2440 | 4.10x real time | preferred Pascal configuration so far |

## Current conclusions

1. The qwentts.cpp/GGML Q8_0 serving path is comfortably faster than real time
   on both tested GPUs. On the GTX 1080 Ti the repeat mean is 0.244 RTF; on the
   RTX 3090 it is 0.148 RTF with the Ampere clamp disabled.
2. Disabling `CLAMP_FP16` on the RTX 3090 reduced the observed repeat mean RTF
   from 0.1655 to 0.1480 (about 10.6% lower RTF). Treat that as a tentative
   runtime-knob result rather than a precision microbenchmark: the generated
   utterance lengths differed substantially and sampling was stochastic.
3. Keep `CLAMP_FP16=1` on Pascal unless a dedicated 1080 Ti experiment shows it
   is unnecessary. Its purpose is hardware robustness, and the current 0.244
   RTF already exceeds the real-time requirement by a wide margin.
4. The end-to-end qwentts/Q8 improvement over the Wavhost/PyTorch FP32 baseline
   is very large (~10.5x lower repeat RTF on the 3090 with clamp off; ~13.5x on
   the 1080 Ti with clamp on), but those numbers combine runtime, kernels, and
   quantization and must not be described as quantization-only speedups.
5. Lower-bit Q4 experiments are no longer required to meet the original
   real-time serving goal. Run them only for a separate throughput/VRAM goal and
   compare retained audio quality.

## Known non-canonical observation

The CSV preserves one 3090 Wavhost run whose dtype chronology was ambiguous.
Those rows have `use_for_summary=no` and `confidence=low`. They are retained so
future archeology does not rediscover the numbers and mistake them for a missing
experiment.
