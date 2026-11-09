# Breeze TTS 2: long-form mathematical narration plan

Status: **planned; no implementation has started**  
Created: 2026-11-09  
Primary repository: **`local-ai`**  
Companion repository: `aiq-dkps-formalization/visualizations`

## Decision: where the work belongs

Keep the master plan here because the main reusable work is in the Breeze serving layer: reference-voice fidelity, inference controls, observable generation limits, and reproducible quality experiments. The **DKPS client owns lecture semantics**: sentence/paragraph segmentation, narration text and pronunciation policy, assembled page/lecture audio, transcript alignment, playback, and artifact caching.

Do **not** build a second general-purpose long-form scheduler in `local-ai` before the model-specific experiments demonstrate a need. Preserve the existing `POST /v1/audio/speech` contract. If an optional multi-segment server API later proves valuable to clients other than DKPS, design it separately after validating the single-segment path.

The desired end state is a coherent 8–15-minute mathematical lecture in one recognizable narrator voice, with no missing spoken material, and with reproducible evidence that the voice and content stay consistent across its constituent inference calls. Keep generation passages, browser playback pages, and a complete lecture recording as **three different units**.

## Findings from the 2026-11-09 local-ai source snapshot

The following items are implemented already; they should be reused rather than rebuilt:

| Existing behavior | Where it lives | Implication |
| --- | --- | --- |
| Breeze candidate deployment, pinned source/model, eager and optional fast CUDA-graph modes | `services/tts/Dockerfile.breeze`, `compose.yaml`, `start-breeze2.sh` | Keep both modes available for controlled comparisons. |
| OpenAI-compatible `/v1/audio/speech`, `/v1/voices`, `/v1/models`, and `/v1/runtime` | `services/tts/scripts/candidate_server.py` | Extend capabilities/diagnostics without breaking standard clients. |
| Named voice references resolved from `<voice>.wav` and `<voice>.txt` | `get_available_voices`, `resolve_voice_files` in `candidate_server.py` | The exact audio/transcript pair is already the server's source of voice identity. |
| Breeze voice-cloning reference, optional instruction, and `cfg_scale` passed to `prepare_inputs` | `Runtime.synthesize`, Breeze branch | Experiment against the existing interface; do not assume a new instruction API is needed. |
| Fast Breeze mode with `FastStreamingConfig(max_new_tokens=750, max_seq_len=2048, fast_all=True, repetition_penalty=1.1)` | `Runtime.__init__` | Inspect actual generation termination and token/audio budgets before increasing segment length. Fast mode enforces repetition penalty 1.1. |
| Eager `model.generate` and fast `iter_audio_chunks` both returned as an encoded WAV | `Runtime.synthesize` | The current HTTP caller receives audio, not an explicit word-coverage or stop-reason report. |
| Voice files mounted read-only into the Breeze container | `services/tts/compose.yaml` (`./voice-references:/voices:ro`) | Prepared references should be published to the existing host voice directory, not generated inside the container. |
| Mechanical WAV checks, smoke tests, run metadata, audio retention and listening review | `scripts/audio_sanity.py`, `scripts/benchmark*.sh`, `scripts/smoke_test.py`, `dev/benchmarks/tts.md`, `EXPERIMENTS.md` | Extend this evidence workflow to long-form quality. No separate benchmarking framework is needed. |

**Important limits of the current evidence:** a valid WAV, plausible duration, successful HTTP response, and a fixed seed cannot demonstrate that all requested words were spoken. The provided server code does not expose an authoritative end-of-text/stop reason or content alignment. Verify whether the pinned upstream Breeze runtime exposes such a signal before designing a response contract around it. A 60-second finished **playback page** is not evidence of truncation by itself; truncation means text was omitted from the resulting speech.

The user currently uses a custom Breeze reference voice (`foobar-v1`) for the DKPS reader. The local DKPS profile was previously tested at `cfg_scale=2.0` with an instructional narrator prompt; the reader's very short 12-word synthesis chunks were introduced to guard against incomplete recordings. Keep that profile as a baseline, not as an assumed quality optimum. The uploaded `local-ai` snapshot defaults the Breeze host port to **11441**; the user's other local setup has also used **8821**. Probe the **actual running endpoint** for each experiment rather than assuming those ports represent identical deployments.

## Goals and required invariants

1. **Complete speech is the first acceptance gate.** Never publish an artifact as complete when a segment ends early, an audio join drops material, or a transcript comparison detects missing mathematical terms.
2. **Stable speaker identity.** Preserve timbre and recognizable narrator characteristics across segment boundaries; do not mistake loudness normalization for voice consistency.
3. **Natural delivery.** Preserve enough semantic context for normal intonation, phrasing and useful pauses without pushing the model into an overlong generation.
4. **Mathematical clarity.** Preserve symbols, names, hypotheses and technical vocabulary; pronunciation substitutions should be explicit and testable.
5. **Reproducibility.** Every generated segment should be traceable to model/runtime revision, reference identity, instruction, settings, text and exact inference mode.
6. **Incremental adoption.** Current Kokoro and Qwen deployments, Breeze single-segment API, and the working DKPS reader must continue functioning. New features are opt-in until validated.
7. **No generated artifacts in Git.** Store private references, WAVs, ASR output, caches and experiment runs in configured untracked locations. Respect voice consent and Breeze model-weight/output license restrictions.

## Implementation sequence

### Phase 0 — capture the real baseline and isolate failure modes

**Owner: `local-ai`, with one DKPS lecture fixture supplied by the client. Priority: first.**

- [ ] Confirm the actual endpoint, model revision, fast/eager mode, voice (`foobar-v1`), exact reference WAV and transcript, and effective request body. Record what `/v1/runtime` currently reports and which fields it does not report.
- [ ] Retain one representative G2 mathematical passage with a variety of sentence lengths and formula pronunciations, plus an adjacent multi-paragraph sequence. Keep the lecture text as a file or hashed external fixture, not a duplicated long-term copy inside this server repo.
- [ ] Reproduce the prior independent **12-word** DKPS request behavior. Retain individual response WAVs, request JSON, per-request duration, full reference identity and seed where provided. Include the actual output that sounded incomplete, if available.
- [ ] Distinguish four outcomes: (a) single response stops speaking before its input ends; (b) response is complete but tone/voice changes between segments; (c) all segments are complete but FFmpeg/normalization/assembly removes material; (d) audio is complete but browser caching, timestamps or playback shows the wrong portion. The browser-cache bug fixed in DKPS must not be counted as model truncation.
- [ ] Add a human listening checklist covering missing final words, repeated words, unnatural restarts, mathematical pronunciation, pace, timbre, loudness, and joins. Require manual review of a representative set even if automatic scores pass.

**Done when:** a retained baseline run can be replayed or regenerated with a manifest that identifies each request, generated audio, and which failure category (if any) it exhibits.

### Phase 1 — reference-voice preparation and runtime diagnostics

**Owner: `local-ai`. Priority: high; can run alongside Phase 2.**

- [ ] Add an opt-in **reference preparation tool** under `services/tts/scripts/` that reads a WAV and its exact transcript, inspects duration/channels/rate/loudness and creates a selected, clean, complete-sentence sample. Compare the raw reference to a version normalized to approximately **−18 LUFS**, following Breeze developer guidance. Do not rewrite the original reference or infer transcript text from the filename.
- [ ] Store selected reference WAV and matching text as a stable pair in the existing ignored `voice-references/` volume. Record a content digest/version for each member of the pair. Make prep repeatable and avoid embedding personal voice recordings in code or tests.
- [ ] Inspect whether the pinned upstream implementation already caches reference conditioning. If it supports a reusable encoded reference representation, benchmark reuse; avoid an additional cache until its memory and correctness behavior are established.
- [ ] Extend diagnostic records around the Breeze inference call with: request ID, model/source pins, voice ID, hashes of reference audio and reference text, instruction identity, `cfg_scale`, seed, input words/characters, inference mode, elapsed time, decoded duration, and generation settings. Do not log private reference audio or full client transcript by default; opt-in experiment artifacts can retain text locally.
- [ ] Determine whether upstream exposes stop reason, generated-token count, token budget exhaustion, or continuation state for **both** eager and fast paths. Record what is genuinely available; derive neither a fake EOS flag nor a universal seconds-per-token limit.
- [ ] Expose safe, static supported capabilities and effective generation limits in `/v1/runtime` or another small diagnostic response, without changing the binary `/v1/audio/speech` success response or revealing host reference paths.

**Likely edit points:** `candidate_server.py`, `scripts/`, `README.md`, and dedicated unit tests. `compose.yaml`/`service.toml` only if the reference or experiment retention layout needs an explicit new persistent path. Keep the service setup/start contract described in `AGENTS.md`.

**Done when:** a voice can be recreated from a recorded reference digest, every experiment identifies the effective runtime settings, and a failure leaves interpretable evidence without exposing private reference audio over HTTP.

### Phase 2 — controlled quality and completeness matrix

**Owner: `local-ai`. Priority: high; establish the winner before rewriting DKPS segmentation.**

Build a focused addition to the existing benchmark workflow, e.g. `services/tts/scripts/benchmark-breeze-longform.py`, that records per-segment artifacts and compares:

| Dimension | Proposed trials |
| --- | --- |
| Passage length | Current 12-word baseline; complete sentences around 25–45 words; longer complete paragraphs sized within measured generation limits. Use character limits too, but let sentence boundaries drive splits. |
| Instruction / CFG | Plain cloning with no performance instruction and CFG **1.0**; current Sagan instruction and CFG **2.0**; a concise mathematical-lecturer direction. |
| Reference | Existing raw `foobar-v1`; clean complete-sentence selected sample; selected + normalized variant, each with exact matching transcript. |
| Inference | Eager versus fast CUDA-graph mode on the same hardware, reference and text. Record exact runtime pins and warmup state. |
| Randomness | Fixed seed for reproducibility and a small set of alternate seeds to quantify between-segment variability. Fixed seed alone is not evidence of continuous voice identity. |
| Joins | No extra silence; roughly 0.15 s; roughly 0.25 s. Check that the sentence itself is complete before adding silence. |

Use paired adjacent passages from the same lecture, not only isolated single-sentence samples. Retain every first/repeat observation according to the existing benchmark policy. Report wall time, decoded audio duration, RTF, peak/RMS/LUFS, silence at start/end, and per-segment generation-limit indicators if available.

- [ ] Add **optional offline ASR comparison** against the exact generated spoken text. Produce a token-level diff highlighting omitted tails, repetitions, substitutions, and mispronounced named mathematical terms. Treat ASR as a screening tool; spot-check reported errors against the WAV.
- [ ] Use human side-by-side/blind listening review for speaker identity, expressiveness, natural pauses and joins. A postprocessing loudness match does not establish a timbre match.
- [ ] Preserve the established `dev/benchmarks/tts.md` + `tts_measurements.csv` raw-run and retention conventions; do not replace existing observations with averaged summaries.
- [ ] Compare upstream continuation/conditioning suggestions only where the **pinned** inference API actually supports them. Research from other runtimes (including Breeze-TTS-2.cpp) motivates an experiment; it is not proof that this Python fast path has the same facility.

**Done when:** one candidate generation policy gives materially better continuity than the 12-word baseline without introducing missing words. Promote only after human listening and content-completeness review.

### Phase 3 — server-side completeness guarantees for an inference segment

**Owner: `local-ai`. Priority: high once baseline evidence is available.**

- [ ] Identify reliable limit/termination indicators in the pinned upstream runtime. Prefer observed finish reason to a wall-clock heuristic. Use strict duration/word-rate checks only as **suspicion signals** when no authoritative indicator exists.
- [ ] On an ambiguous or likely incomplete response, return an **explicit failure/diagnostic result** rather than HTTP 200 plus a plausible but partial WAV. Do not silently truncate, pad, or call clipped speech complete. Separate inference failure from client timeout and media encoding failure.
- [ ] Keep a small server-level retry only for transient inference/transport problems. Text splitting and the target passage size should remain controlled by the caller's long-form policy (DKPS Phase 4), unless measurement demonstrates a broadly reusable backend-specific need.
- [ ] Ensure audio conversion uses the entire decoded waveform; measure expected and actual sample counts, and retain the unprocessed WAV in opted-in experiments to distinguish model omissions from ffmpeg clipping.
- [ ] Preserve `/v1/audio/speech` compatibility. If later exposing segment diagnostics for clients, use a backward-compatible opt-in response/side channel rather than silently changing MIME type.

**Done when:** simulated truncated WAVs, generation caps and conversion failures cannot be cached or returned as successful completed speech, while normal existing clients still work.

### Phase 4 — DKPS client: sentence/paragraph passage policy and lecture assembly

**Owner: `aiq-dkps-formalization`, not this overlay. Priority: after Phase 2 candidate selection.**

Target files to inspect in DKPS: `visualizations/dkvis/presentation/audio_library.py`, `audio.py`, `tts.py`, `serve.py`, `audio_library.html`, and their tests. Reconcile with the current DKPS checkout before editing; this local-ai source archive does not contain those files.

- [ ] Distinguish **generation passage** (model request) from **reader page** (navigation) and **whole lecture** (assembled audio). A 600-character reader page may require several backend requests; a model-optimal paragraph may span a reader boundary if regeneration and mapping are handled coherently.
- [ ] Preserve complete sentences when a passage fits; prefer paragraph breaks. Split oversized sentences only when required by a measured backend budget. Preserve mathematical punctuation, abbreviations, quoted text and exact spoken-text ordering. Pronunciation expansion must be counted in the text actually sent to the server.
- [ ] Make segment limits a per-voice/per-model policy with a conservative fallback and an optional adaptive size reduction when a segment fails. Bound retries, and report unresolved chunks rather than stitching partial speech into the published lecture.
- [ ] Reuse the selected reference and instruction policy across the entire lecture. Do not repeat theatrical performance directions at every segment by default; test shorter persistent style controls versus undirected cloning.
- [ ] Assemble losslessly into a single seekable recording (or a page-sized independently addressable container). Check that the decoded output duration matches the input segment durations and explicit pauses, within codec tolerance. Never let transcript-highlighting estimates decide when to advance audio.
- [ ] Keep an exact *generation manifest* with source sentence/word offsets, segment boundaries, voice-reference version, backend/model revisions, instructions, CFG, seed policy, per-segment duration, completeness status, output hashes, and optional ASR/forced-alignment results.
- [ ] Cache by transcript text, segmentation algorithm and thresholds, pronunciation map, voice reference digest, model/runtime pins, inference parameters, postprocessing settings, and assembled-audio format. Keep browser audio URLs content-addressed; changing any of these must avoid stale browser playback.
- [ ] Add repair controls for re-synthesizing an individual rejected section without regenerating the whole lecture; ensure rebuilt pages and timestamps reflect the replacement. Make whole-lecture download/export a separate operation from interactive playback.

**Done when:** a long lecture plays continuously, with correct content and complete endings, and each intermediate segment can be inspected/repaired independently without losing the reader's progress and highlighting semantics.

### Phase 5 — continuity conditioning, only if justified by experiments

**Owner: start in `local-ai` if backend-specific; call from DKPS only after supported.**

- [ ] Investigate whether the **pinned Python Breeze** backend can condition a new passage on a prior segment, a processed reference embedding, or a separate style state. Do not assume the community C++ implementation's strategy is directly supported by this implementation.
- [ ] Compare reusing a fixed curated reference against conditioning on the first approved lecture segment, or on a bounded preceding context, when an actual API exists. Measure speaker drift, text duplication, and error propagation; previous generated material must not replace the immutable original reference by default.
- [ ] If this proves beneficial, expose an optional, versioned backend-specific capability while keeping the plain OpenAI-compatible request path supported. Avoid requiring stateful server sessions for normal one-shot clients.

**Done when:** continuity conditioning passes the same completeness and listening gates as the simpler independent-passage approach. Otherwise retain the simpler approach.

## Validation matrix and promotion gates

Minimum coverage:

- **Transcript coverage:** no known omitted or duplicated ending in retained benchmark samples; exact spoken input remains traceable to the source. ASR flags require manual adjudication for formulas and proper nouns.
- **Truncation:** cap hit, plausible-duration partial WAV, corrupted data, slow generation, premature EOS, and silent/empty audio have distinct test cases. A failed generation must not be marked ready.
- **Voice identity:** several adjacent chunks from `foobar-v1` and at least one other available authorized reference; compare voice drift with fixed and varied seeds, and with/without instruction.
- **Join quality:** no clipped final consonants, missing inter-sentence pauses, audible clicks, cumulative silence drift, or abrupt speaker change at joins.
- **Configurations:** fast and eager generation; reference without its required transcript rejected explicitly; unsupported model/voice rejected explicitly; reference files remain read-only and private.
- **Regression:** other TTS backends and the existing `/v1/audio/speech` endpoint remain unchanged by default; the existing `setup.sh` / `start.sh` deployment contract remains intact.
- **Performance:** RTF, peak GPU memory, warmup/startup and retry counts reported for real hardware. The 2026-11-04 benchmark ledger records Breeze on RTX 3090 at approximately 3.50 eager RTF and 0.54 fast RTF; these are prior reference measurements, not the acceptance values for the new workload.
- **Promotion:** require a controlled listening comparison favoring the candidate or showing a clear operational benefit, zero confirmed missing text in the review set, and repeatable provenance. Do not select based solely on benchmark speed.

## Handoff map (future implementation changes)

| Work | Repository and likely files | Deliverable |
| --- | --- | --- |
| Baseline fixtures, segment matrix, human listening checklist, optional ASR audit | `local-ai/services/tts/scripts/`, `dev/benchmarks/tts.md`, experiment artifact directory | Reproducible evidence + selected settings |
| Curated reference selection/normalization and content hashes | `local-ai/services/tts/scripts/`, gitignored `voice-references/` | Stable speaker conditioning identity |
| Breeze stop-reason/limit instrumentation and fail-closed audio responses | `local-ai/services/tts/scripts/candidate_server.py` and unit tests | Better observability and response completeness |
| Optional reference-state reuse or continuation | `local-ai/services/tts/scripts/candidate_server.py` after upstream API check | Gated, opt-in backend improvement |
| Sentence/paragraph passage planner, adaptive bounded retries, caching, assembly and repair | DKPS `visualizations/dkvis/presentation/` and tests | Continuous, inspectable lecture output |
| Transcript highlighting, full-lecture player/export, per-page progress | DKPS audio reader UI/API | No stale audio or premature page transitions |

**First implementation slice:** implement Phase 0's retained benchmark harness and Phase 1's reference/diagnostic inspection, then run the small quality matrix. Do not rewrite the DKPS reader or add a stateful long-form server API before this evidence is available.

## Questions to resolve during the first slice

1. Does the pinned eager and/or fast Breeze runtime report stop reason, EOS, generated token count, remaining budget, or reusable continuation state? Which information is safely exposed to a caller?
2. Which actual endpoint and container served the `foobar-v1` voice when the original one-minute clips were produced? Were they model-truncated, pre/postprocessed, assembled incorrectly, or stale browser audio?
3. Does selected/normalized reference audio with the same exact text reduce timbre drift? Does CFG 1.0/no instruction outperform CFG 2.0 with the current mathematical-lecturer instruction?
4. What passage lengths reliably finish on the target GPU in **both** eager and fast modes? Is there a quality window before the model exhausts generation tokens or loses text coverage?
5. Is continuation conditioning available in the pinned Python implementation, or would it require upstream changes? Would it be worth the added state and failure modes?
6. Should the final publishing unit be one audio file per lecture with an index, or independently cached page files plus a compiled export? Base this on DKPS seeking/repair measurements.

## External starting points

These are research references to inspect while implementing; the pinned local Python server is authoritative for its own available APIs.

- Breeze TTS 2 upstream: <https://github.com/breezeblue-ai/breeze-tts>. See especially issue #25 on voice inconsistency across segments: <https://github.com/breezeblue-ai/breeze-tts/issues/25>.
- Breeze-TTS-2.cpp long-text CLI and server behaviors (a **separate runtime**, not the current local-ai backend): <https://github.com/HoppouAI/Breeze-TTS-2.cpp/blob/main/docs/cli.md> and <https://github.com/HoppouAI/Breeze-TTS-2.cpp/blob/main/docs/server.md>.
- TTS-Story's Breeze engine integration and chunk review/repair workflow: <https://github.com/Xerophayze/TTS-Story/blob/main/docs/help/engines/breeze-tts-2.md> and <https://github.com/Xerophayze/TTS-Story>.

## Status ledger

| Phase | State | Evidence / next action |
| --- | --- | --- |
| 0 Baseline | Not started | Retain a verified multi-segment G2 sample and actual Sagan request profile. |
| 1 References/diagnostics | Not started | Inventory upstream metadata/conditioning and reference processing. |
| 2 Quality comparison | Not started | Run the paired short/medium/paragraph, CFG and reference experiments. |
| 3 Segment validation | Not started | Implement detection only after establishing supported signals. |
| 4 DKPS long-form client | Not started | Handoff after the best generation policy is selected. |
| 5 Continuity conditioning | Research option | Implement only after proving better output quality. |

This document is a standalone plan. Keep it current as experiments resolve questions; do not clobber unrelated backlogs or convert hypothetical tasks into completion claims.
