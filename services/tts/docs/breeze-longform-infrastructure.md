# Breeze long-form narration: first implementation slice

Status: **implemented reusable passage planning and equal-text listening evaluation infrastructure; cadence review and production integration pending** (2026-11-09).

This document accompanies `breeze-longform-lecture-plan.md`. It does not replace or rewrite that plan. All tools below are **opt-in and host-side**. `candidate_server.py`, service lifecycle scripts, model pins, and the OpenAI-compatible speech endpoint are unchanged.

## Tools

| Tool | Purpose | Output |
| --- | --- | --- |
| `scripts/longform_benchmark.py plan` | Show deterministic, sentence-preserving passage plans and equal-text coverage without contacting a server | JSON summary on stdout; no audio |
| `scripts/longform_benchmark.py run` | Compare the same complete excerpt under each selected policy; query `/v1/models`, `/v1/runtime`, and `/v1/audio/speech` against a *running* Breeze service, retaining every request, WAV, duration, wall time, RTF, model pin, runtime mode, and error | Private artifact run directory outside Git |
| `scripts/prepare_breeze_reference.py inspect` | Report PCM16 WAV duration, channel/rate, RMS, peak, edge silence, and hashes of WAV and exact transcript | No file changes |
| `scripts/prepare_breeze_reference.py prepare` | Explicitly copy an authorized WAV/text pair, or select a user-specified time window and optionally apply FFmpeg −18 LUFS loudness normalization | `<name>.wav`, `<name>.txt`, `<name>.reference.json` in an ignored/private destination |
| `scripts/passage_planner.py` | Balance intact sentences around soft word targets, with hard safety bounds and explicit boundary metadata; never generate speech | Imported by benchmark runner |
| `scripts/test_longform_benchmark.py` | Run offline tests with a fake local speech HTTP endpoint, synthetic WAVs and private temporary files | No GPU or voice assets required |

### Compare short and longer narration passages

From `local-ai/`, run a dry plan:

```bash
python3 services/tts/scripts/longform_benchmark.py plan \
    --input services/tts/fixtures/longform-math.txt
```

Then, with a Breeze server already running and an authorized voice installed, compare the **same complete fixture** across all three long-form variants:

```bash
python3 services/tts/scripts/longform_benchmark.py run \
    --url http://127.0.0.1:8821 \
    --voice "${TTS_BENCH_VOICE:?Set your local benchmark voice first}" \
    --input services/tts/fixtures/longform-math.txt \
    --output-dir "$HOME/.cache/local-ai/tts-longform"
```

For a smaller smoke test, add `--max-segments 3`. This now selects the **longest complete-sentence prefix common to every selected variant**, with no more than three synthesis calls per variant. The complete 148-word fixture is not covered by this reduced run. Use `--variant short12` explicitly to include the short baseline, or multiple `--variant` flags to choose a subset. The default has changed from four variants to `plain40`, `directed40`, and `paragraph80`; no `--max-segments` limit means full input coverage.

`foobar` is a **distributable example name**, not an included voice. Set `TTS_BENCH_VOICE` privately at runtime; do not put personal voice identifiers, WAVs, transcripts, instructions, or experiment output in Git. An unset or empty variable now aborts before the benchmark contacts the server. The server base URL is shown for a local deployment exposing host port `8821`; use the actual port reported by `docker ps` if it differs. For a real lecture, point `--input` at a private extracted spoken transcript outside this server repository. A larger input provides more adjacent passages for meaningful comparisons.

The first checks after a failed benchmark should be:

```bash
if [ -n "${TTS_BENCH_VOICE:-}" ]; then echo 'Voice variable set'; else echo 'Voice variable EMPTY'; fi
python3 services/tts/scripts/longform_benchmark.py report \
  --run-dir "$HOME/.cache/local-ai/tts-longform/<your-run-directory>"
```

`report` also understands retained manifests from the initial version of this tool, including the negative-prompt CFG error. It groups errors by HTTP status or WAV-validation type without printing stored voice identifiers, input prose, or server paths. The full HTTP error body is preserved in each **private** segment's `error.json` for local debugging. If the server returns a request-level HTTP 400/401/403/404/422, the benchmark stops instead of repeating the same failure for every segment. A run with any failed requests returns a nonzero CLI exit status. Neither endpoint discovery nor voice validation proves that an installed reference has a matching transcript, so a request can still fail and provide that specific detail in the private error record.

The policies are `short12` (soft target 12 words, hard maximum 24, CFG 1.0), `plain40` (soft target 40, hard maximum 52, CFG 1.0), `directed40` (soft target 40, hard maximum 52, CFG 2.0 with a generic direction), and `paragraph80` (soft target 80, hard maximum 110, CFG 1.0). The short baseline is opt-in. All preserve complete sentences and favor paragraph ends, with dynamic-programming balancing to avoid tiny trailing requests. Only a sentence longer than the **hard maximum** is split at word boundaries; oversized sentences are balanced evenly to avoid one-word tails. The planner retains the full ordered input and records any forced split. These values are experimental, **not validated production limits**. Each request uses the same reference identity, seed (42 by default), and variant-specific generation settings. Breeze reference-clone templates without an instruction lack a negative prompt, so CFG > 1 is incompatible with that request mode. The benchmark now checks this before making requests. A directed variant may use CFG 2.0 because the instruction selects a compatible template. This is an inference-template requirement, not evidence that the voice or server is faulty.

For a faithful baseline of an existing configured narrator, pass the actual custom instruction from a **private external text file** using `--baseline-instruction-file`. That instruction replaces the generic/no-instruction settings of `short12` and `directed40`, leaving undirected `plain40` and `paragraph80` intact. The instruction is retained only in the local run artifacts. Optional `--reference-wav` and `--reference-transcript` compute reference-pair hashes and record mechanical characteristics; both must be supplied together. They do **not** upload the WAV to the server, replace the server's installed reference, or prove reference/text alignment.

Do not compare fast and eager samples as though they came from one server mode. Run the same set against each separately started runtime and compare their individual `manifest.json` files. The runtime mode and model/source pins are recorded when the endpoint reports them.

Every run retains `input.txt` (full source), `evaluated.txt` (the exact common excerpt), `manifest.json` with shared excerpt digest and word coverage, `results.json`, `LISTEN_REVIEW.tsv`, `JOIN_REVIEW.tsv`, and `joins.json`. Each variant has `plan.json` (passage text, word offsets, paragraph and forced-sentence-split flags), `joined.wav`, `joined.metrics.json`, and `join-001.wav`/`.json` etc. for **focused listening around each transition**. Each per-segment folder has its private `request.json`, `audio.wav`, and `metrics.json` or `error.json`.

The joined WAV concatenates original decoded PCM16 without re-encoding or loudness correction. Its pauses depend on boundaries: `--join-pause` (default 0.15 s) between sentences, `--paragraph-pause` (default 0.30 s) between paragraphs, and **zero extra pause** at an unavoidable forced mid-sentence boundary. The transition previews retain up to three seconds on either side of each join. If any segment fails, no joined preview is published. These pauses are listening experiments, not production defaults; they do not remove or alter existing silence in the generated audio. The output frame count is checked against the inputs and inserted silence.

The review sheets prioritize **cadence within sentences**, naturalness across joins, and stability of speaker identity. Keep pronunciation notes separate. An HTTP 200 and syntactically complete WAV cannot prove that all requested words were spoken; coverage checking remains a manual review. The `report` command reports coverage, duration and loudness measurements without echoing the voice or text. Human ratings of cadence and delivery are needed before selecting a production policy.

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
- **Phase 2:** equal-text comparisons, soft-target sentence planning, full-coverage manifests, and focused join-review WAVs are implemented. Listening judgments and a selected default policy remain open. ASR is optional and separate from cadence evaluation.
- **Phase 3:** no changes to inference termination or the binary HTTP response have been made; investigate actual upstream signals before hardening success/failure logic.
- **Phase 4:** DKPS owns production passage/page/lecture assembly and cache/playback work after evaluation. This local-ai planner is experimental and pure; do not wire it into serving or duplicate the production scheduler without an explicit integration plan.
- **Phase 5:** the planner retains passage offsets, paragraph boundaries, and context relationships for future work. It sends **only the words to be spoken** to Breeze: preceding narration is not prepended to requests. Prior-audio conditioning remains a research option, not an assumed supported API.

Generated audio and reference material stay outside Git. Experiment manifests intentionally contain selected voice identifiers, full spoken text, delivery instructions, and WAV paths within **private artifact directories**, not committed documentation.

Run offline tests with:

```bash
python3 -m unittest discover -s services/tts/scripts -p 'test_longform_benchmark.py' -v
```
