# Breeze long-form narration: first implementation slice

Status: **implemented Phase 0 scaffolding and Phase 1 reference inspection; GPU quality evaluation pending** (2026-11-09).

This document accompanies `breeze-longform-lecture-plan.md`. It does not replace or rewrite that plan. All tools below are **opt-in and host-side**. `candidate_server.py`, service lifecycle scripts, model pins, and the OpenAI-compatible speech endpoint are unchanged.

## Tools

| Tool | Purpose | Output |
| --- | --- | --- |
| `scripts/longform_benchmark.py plan` | Show deterministic, sentence-aware text passages for four baseline variants without contacting a server | JSON summary on stdout; no audio |
| `scripts/longform_benchmark.py run` | Query `/v1/models`, `/v1/runtime`, and `/v1/audio/speech` against a *running* Breeze service, retaining every request, WAV, duration, wall time, RTF, model pin, runtime mode, and error | Private artifact run directory outside Git |
| `scripts/prepare_breeze_reference.py inspect` | Report PCM16 WAV duration, channel/rate, RMS, peak, edge silence, and hashes of WAV and exact transcript | No file changes |
| `scripts/prepare_breeze_reference.py prepare` | Explicitly copy an authorized WAV/text pair, or select a user-specified time window and optionally apply FFmpeg −18 LUFS loudness normalization | `<name>.wav`, `<name>.txt`, `<name>.reference.json` in an ignored/private destination |
| `scripts/test_longform_benchmark.py` | Run offline tests with a fake local speech HTTP endpoint, synthetic WAVs and private temporary files | No GPU or voice assets required |

### Compare short and longer narration passages

From `local-ai/`, run a dry plan:

```bash
python3 services/tts/scripts/longform_benchmark.py plan \
    --input services/tts/fixtures/longform-math.txt
```

Then, with a Breeze server already running and an authorized `foobar` voice installed:

```bash
python3 services/tts/scripts/longform_benchmark.py run \
    --url http://127.0.0.1:8821 \
    --voice "${TTS_BENCH_VOICE:?Set your local benchmark voice first}" \
    --input services/tts/fixtures/longform-math.txt \
    --output-dir "$HOME/.cache/local-ai/tts-longform" \
    --max-segments 3
```

`foobar` is a **distributable example name**, not an included voice. Set `TTS_BENCH_VOICE` privately at runtime; do not put personal voice identifiers, WAVs, transcripts, instructions, or experiment output in Git. An unset or empty variable now aborts before the benchmark contacts the server. The server base URL is shown for a local deployment exposing host port `8821`; use the actual port reported by `docker ps` if it differs. For a real lecture, point `--input` at a private extracted spoken transcript outside this server repository. A larger input provides more adjacent passages for meaningful comparisons.

The first checks after a failed benchmark should be:

```bash
if [ -n "${TTS_BENCH_VOICE:-}" ]; then echo 'Voice variable set'; else echo 'Voice variable EMPTY'; fi
python3 services/tts/scripts/longform_benchmark.py report \
  --run-dir "$HOME/.cache/local-ai/tts-longform/<your-run-directory>"
```

`report` also understands retained manifests from the initial version of this tool. It groups errors by HTTP status or WAV-validation type without printing stored voice identifiers, input prose, or server paths. The full HTTP error body is preserved in each **private** segment's `error.json` for local debugging. If the server returns a request-level HTTP 400/401/403/404/422, the benchmark stops instead of repeating the same failure for every segment. A run with any failed requests returns a nonzero CLI exit status. Neither endpoint discovery nor voice validation proves that an installed reference has a matching transcript, so a request can still fail and provide that specific detail in the private error record.

The four initial variants are `short12` (12 words, CFG 2.0, no instruction by default), `plain40` (40 words, CFG 1.0, no instruction), `directed40` (40 words, CFG 2.0, concise generic direction), and `paragraph80` (80 words, CFG 1.0). Each uses sentence boundaries where possible and the same seed (42 by default). Long sentences still require word-boundary splitting. These numbers are **trial settings**, not validated optimal generation budgets.

For a faithful baseline of an existing configured narrator, pass the actual custom instruction from a **private external text file** using `--baseline-instruction-file`. That instruction replaces the generic/no-instruction settings of `short12` and `directed40`, leaving undirected `plain40` and `paragraph80` intact. The instruction is retained only in the local run artifacts. Optional `--reference-wav` and `--reference-transcript` compute reference-pair hashes and record mechanical characteristics; both must be supplied together. They do **not** upload the WAV to the server, replace the server's installed reference, or prove reference/text alignment.

Do not compare fast and eager samples as though they came from one server mode. Run the same set against each separately started runtime and compare their individual `manifest.json` files. The runtime mode and model/source pins are recorded when the endpoint reports them.

Each run directory contains `input.txt`, `manifest.json`, `results.json`, `LISTEN_REVIEW.tsv`, `joins.json`, per-variant `joined.wav` and `joined.metrics.json` when every segment succeeds, and `VARIANT/SEGMENT/request.json`, `audio.wav`, `metrics.json` or `error.json`. The results have status `needs_listening_review`, even when WAV structural checks pass. The `joined.wav` preview concatenates decoded PCM16 without re-encoding, with a configurable `--join-pause` (default 0.15 seconds). If any segment fails, no joined preview is published. Listen for missing/duplicated words, changed speaker identity, tempo, mathematical pronunciations and joins, and fill the review sheet manually. Tests also verify that the output frame count equals the sum of the inputs and inserted silence. **Do not promote a candidate solely because its HTTP response and audio duration were valid.**

### Inspect and prepare references

```bash
python3 services/tts/scripts/prepare_breeze_reference.py inspect \
    --wav services/tts/voice-references/foobar.wav \
    --transcript services/tts/voice-references/foobar.txt
```

The optional `prepare` command requires `--output-dir`, `--name` and both input paths. Trimming additionally requires `--start`, `--end`, and `--selected-transcript` containing the **actual words** in that selected interval. `--normalize` uses FFmpeg's `loudnorm` filter with a −18 LUFS integrated target; measure the output and listen before assuming the target was reached. Neither operation modifies its input files. No forced alignment or transcription is attempted.

The recommended destination is the existing, ignored `services/tts/voice-references/` mount; an external data directory is also acceptable. The tool never puts generated WAVs in tracked source directories and will not overwrite an existing voice pair without `--force`. A manifest is written after the pair. The user remains responsible for ensuring voice consent and exact transcript correspondence.

## Current boundaries and next implementations

- **Phase 0:** script, fixture, requests, retention, mechanical validation, human review sheet, and fake-service tests are implemented. Real hardware/voice results are pending.
- **Phase 1:** reference inspection, explicit trimming/normalization, hashes, and static runtime capture are implemented. Upstream conditioning reuse and stop-reason/token-budget discovery are still open.
- **Phase 2:** the test matrix is runnable. ASR auditing, paired listening results, and optimal policy selection remain open.
- **Phase 3:** no changes to inference termination or the binary HTTP response have been made; investigate actual upstream signals before hardening success/failure logic.
- **Phase 4:** DKPS owns passage/page/lecture assembly and cache/playback work after evaluation; do not clone that scheduler into this service.
- **Phase 5:** previous-audio conditioning remains a research option, not an assumed supported API.

Generated audio and reference material stay outside Git. Experiment manifests intentionally contain selected voice identifiers, full spoken text, delivery instructions, and WAV paths within **private artifact directories**, not committed documentation.

Run offline tests with:

```bash
python3 -m unittest discover -s services/tts/scripts -p 'test_longform_benchmark.py' -v
```
