#!/usr/bin/env python3
"""Retained, opt-in Breeze long-form benchmark using the existing HTTP API.

Only standard-library dependencies. A server is not needed to plan a run or
exercise the tests. Generated media and speaker identifiers stay in an
explicit artifact directory outside the source checkout.
"""
from __future__ import annotations

import argparse
import array
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import sys
import time
import urllib.error
import urllib.request
import wave

from passage_planner import (plan_passages, sentences, shared_excerpt, normalize)

HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
DEFAULT_EXCERPT = HERE.parents[1] / 'fixtures' / 'longform-math.txt'
VERSION = 3
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
# These are *benchmarks*, not validated defaults for production serving.
# target_words is a soft objective; max_words is a separate hard safety bound.
# Default trials compare the complete same excerpt. short12 is a diagnostic
# baseline, not a recommended production configuration.
VARIANTS = {
    'short12': dict(target_words=12, max_words=24, cfg_scale=1.0, instruction=None),
    'plain40': dict(target_words=40, max_words=52, cfg_scale=1.0, instruction=None),
    'directed40': dict(target_words=40, max_words=52, cfg_scale=2.0,
                       instruction='Speak in a natural, measured mathematical lecture style.'),
    'paragraph80': dict(target_words=80, max_words=110, cfg_scale=1.0, instruction=None),
}
DEFAULT_VARIANTS = ['plain40', 'directed40', 'paragraph80']


def passages(text: str, max_words: int) -> list[str]:
    """Compatibility wrapper for strict-budget planning and existing callers."""
    return [p.text.replace('\n\n', ' ') for p in
            plan_passages(text, target_words=max_words,
                          hard_max_words=max_words)]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    try:
        temp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def inspect_wav_bytes(wav_data: bytes) -> dict:
    """Measure decoded PCM16 including edge silence; never claim word coverage."""
    with wave.open(io.BytesIO(wav_data), 'rb') as source:
        channels, width = source.getnchannels(), source.getsampwidth()
        rate, frames = source.getframerate(), source.getnframes()
        payload = source.readframes(frames)
    if channels < 1 or width != 2 or rate <= 0 or frames <= 0:
        raise ValueError('expected nonempty PCM16 WAV with positive sample rate')
    if len(payload) != frames * channels * 2:
        raise ValueError('incomplete PCM16 WAV body')
    samples = array.array('h')
    samples.frombytes(payload)
    if sys.byteorder != 'little':
        samples.byteswap()
    peak = max(abs(x) for x in samples) / 32768
    squares = sum(x * x for x in samples)
    rms = math.sqrt(squares / len(samples)) / 32768
    # RMS dBFS and edge silence are descriptive, not integrated EBU LUFS.
    threshold = 32768 * 10 ** (-45 / 20)
    def silent_at(frame_index: int) -> bool:
        start = frame_index * channels
        return max(abs(x) for x in samples[start:start+channels]) < threshold
    left = 0
    while left < frames and silent_at(left):
        left += 1
    right = 0
    while right < frames and silent_at(frames - right - 1):
        right += 1
    length = frames / rate
    return dict(duration_seconds=round(length, 4), sample_rate=rate,
                channels=channels, frames=frames, pcm_bits=16,
                peak=round(peak, 6), rms=round(rms, 6),
                rms_dbfs=round(20 * math.log10(rms), 3) if rms else None,
                leading_silence_seconds=round(left / rate, 4),
                trailing_silence_seconds=round(right / rate, 4),
                # Long or silent audio is a warning; no HTTP-success guarantee.
                mechanical_warnings=(['silent_audio'] if rms < 0.0001 else [])
                + (['duration_over_60s'] if length >= 59.5 else []))


def inspect_wav(path: Path) -> dict:
    return inspect_wav_bytes(path.read_bytes())


def join_wavs(paths: list[Path], output: Path, *,
              pause_seconds: float | list[float]) -> dict:
    """Build a listening preview without re-encoding or discarding samples.

    PCM16 inputs must agree on rate/channels; a mismatch is reported rather
    than silently resampling one of the experimental outputs.
    """
    if not paths:
        raise ValueError('joining requires audio files')
    pauses = ([float(pause_seconds)] * (len(paths)-1) if isinstance(pause_seconds, (int,float))
              else list(pause_seconds))
    if len(pauses) != len(paths)-1 or any(p < 0 or not math.isfinite(p) for p in pauses):
        raise ValueError('joining requires one finite nonnegative pause per boundary')
    pcm = []
    spec = None
    expected_frames = 0
    for path in paths:
        with wave.open(str(path), 'rb') as wav:
            current = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
            if current[1] != 2:
                raise ValueError('joining requires PCM16 source WAVs')
            if spec is not None and current != spec:
                raise ValueError('WAV sample format mismatch across segments')
            spec = current
            frames = wav.getnframes()
            content = wav.readframes(frames)
            if len(content) != frames * current[0] * current[1]:
                raise ValueError('incomplete input WAV')
            pcm.append(content)
            expected_frames += frames
    assert spec is not None
    silent_frames = [round(pause * spec[2]) for pause in pauses]
    expected_frames += sum(silent_frames)
    with wave.open(str(output), 'wb') as merged:
        merged.setnchannels(spec[0])
        merged.setsampwidth(spec[1])
        merged.setframerate(spec[2])
        for idx, data in enumerate(pcm):
            if idx:
                merged.writeframesraw(b'\0' * (silent_frames[idx-1] * spec[0] * spec[1]))
            merged.writeframesraw(data)
    with wave.open(str(output), 'rb') as joined:
        if joined.getnframes() != expected_frames:
            output.unlink(missing_ok=True)
            raise ValueError('joined WAV lost frames')
    measurements = inspect_wav(output)
    measurements['joined_segments'] = len(paths)
    measurements['join_pauses_seconds'] = pauses
    measurements['pause_seconds'] = pause_seconds if isinstance(pause_seconds,(int,float)) else None
    measurements['expected_frames'] = expected_frames
    return measurements


def boundary_preview(previous: Path, following: Path, output: Path, *,
                     pause_seconds: float, context_seconds: float = 3.0) -> dict:
    """Write a short, lossless listening snippet around a speech join.

    The last context_seconds of the prior segment and the first context_seconds
    of the next segment are retained. These previews support focused review of
    timbre, tempo, intonation and transition timing without ASR inference.
    """
    if not (math.isfinite(context_seconds) and context_seconds > 0):
        raise ValueError('join context must be finite and positive')
    if not (math.isfinite(pause_seconds) and pause_seconds >= 0):
        raise ValueError('join pause must be finite and nonnegative')
    parts = []
    spec = None
    for path, tail in [(previous, True), (following, False)]:
        with wave.open(str(path), 'rb') as source:
            fmt = source.getnchannels(), source.getsampwidth(), source.getframerate()
            if fmt[1] != 2 or (spec is not None and spec != fmt):
                raise ValueError('join previews require matching PCM16 WAV formats')
            spec = fmt
            total = source.getnframes()
            keep = min(total, max(1, round(context_seconds * fmt[2])))
            if tail:
                source.setpos(total - keep)
            pcm = source.readframes(keep)
            if len(pcm) != keep * fmt[0] * fmt[1]:
                raise ValueError('join preview found incomplete WAV data')
            parts.append((keep, pcm))
    assert spec is not None
    quiet = round(pause_seconds * spec[2])
    expected_frames = parts[0][0] + quiet + parts[1][0]
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(spec[0])
        wav.setsampwidth(spec[1])
        wav.setframerate(spec[2])
        wav.writeframesraw(parts[0][1])
        wav.writeframesraw(b'\0' * (quiet * spec[0] * spec[1]))
        wav.writeframesraw(parts[1][1])
    metrics = inspect_wav(output)
    if metrics['frames'] != expected_frames:
        output.unlink(missing_ok=True)
        raise ValueError('join preview lost audio samples')
    return dict(expected_frames=expected_frames,
                before_context_seconds=round(parts[0][0]/spec[2], 4),
                after_context_seconds=round(parts[1][0]/spec[2], 4),
                pause_seconds=pause_seconds,
                duration_seconds=metrics['duration_seconds'])


def _request(url: str, *, payload: dict | None = None, timeout: float = 20) -> tuple[bytes, dict]:
    headers = {'Accept': 'application/json' if payload is None else 'audio/wav'}
    data = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(payload).encode('utf8')
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method='POST' if payload is not None else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            chunks = []
            total = 0
            while block := response.read(256 * 1024):
                total += len(block)
                if total > MAX_RESPONSE_BYTES:
                    raise ValueError('audio response exceeds 64 MiB')
                chunks.append(block)
            content = b''.join(chunks)
            expected = response.headers.get('Content-Length')
            if expected is not None and int(expected) != len(content):
                raise ValueError('audio response has incomplete content length')
            return content, {'status': response.status, 'content_type': response.headers.get('Content-Type', '')}
    except urllib.error.HTTPError as exc:
        detail = exc.read(512).decode('utf8', 'replace')
        raise RequestFailure(exc.code, detail) from exc


class RequestFailure(RuntimeError):
    """HTTP failure with the response kept privately, not printed to stdout."""
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f'HTTP {status}: {detail}')


def failure_label(exc: Exception) -> str:
    """Short diagnostic without private speaker names or server paths."""
    if isinstance(exc, RequestFailure):
        if exc.status in (400, 422) and 'does not define a negative prompt' in exc.detail.lower():
            return f'HTTP {exc.status}: CFG > 1 requires an instruction-compatible Breeze template'
        reason = ('invalid request, voice, or reference transcript' if exc.status in (400, 422)
                  else 'authentication or permission rejected' if exc.status in (401, 403)
                  else 'speech endpoint not found' if exc.status == 404
                  else 'speech server synthesis failure' if exc.status >= 500
                  else 'speech request rejected')
        return f'HTTP {exc.status}: {reason}'
    if isinstance(exc, wave.Error):
        return 'invalid WAV returned by server'
    if isinstance(exc, urllib.error.URLError):
        return 'speech endpoint connection failed'
    if isinstance(exc, ValueError):
        return 'invalid response or audio measurement'
    return 'speech generation failed'


def variant_instruction(name: str, baseline_instruction: str | None = None) -> str | None:
    """Return the effective instruction for a benchmark trial."""
    if baseline_instruction and name in ('short12', 'directed40'):
        return baseline_instruction
    return VARIANTS[name]['instruction']


def validate_breeze_cfg(cfg_scale: float, instruction: str | None) -> None:
    """Reject unsupported reference-clone CFG combinations before inference.

    The ref_clone_tata template has no negative prompt. Supplying a guidance
    scale greater than one without an instruction selects that template and
    Breeze rejects the request. An instruction selects a different template.
    """
    if not math.isfinite(cfg_scale) or cfg_scale <= 0:
        raise ValueError('Breeze CFG scale must be positive and finite')
    if cfg_scale > 1.0 and not instruction:
        raise ValueError('Breeze CFG > 1 requires a nonempty instruction; '
                         'use CFG 1 for undirected reference cloning')


def validate_voice(voice: str, model_info: dict) -> None:
    """Reject blank or unlisted voices before creating an artifact directory."""
    if not isinstance(voice, str) or not voice.strip():
        raise ValueError('--voice is empty. Set TTS_BENCH_VOICE locally or supply --voice.')
    if voice != voice.strip() or any(ord(ch) < 32 for ch in voice):
        raise ValueError('--voice contains whitespace or control characters')
    available = model_info.get('speakers')
    if isinstance(available, list) and available:
        if voice.casefold() not in {str(name).casefold() for name in available}:
            raise ValueError('--voice is not listed in /v1/models speakers; check its local value')


def discover(url: str, *, voice: str | None = None) -> tuple[str, dict]:
    try:
        raw, _ = _request(url + '/v1/models')
        model_info = json.loads(raw)['data'][0]
        model = model_info['id']
        if voice is not None:
            validate_voice(voice, model_info)
    except (KeyError, IndexError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError(f'cannot discover model from {url}: {exc}') from exc
    raw, _ = _request(url + '/v1/runtime')
    runtime = json.loads(raw)
    # Retain only metadata needed for comparisons. Avoid server file paths,
    # private reference filenames, and other deployment-specific details.
    safe = ['backend', 'model_alias', 'source_revision', 'model_revision',
            'breeze_fast', 'sample_rate', 'effective_dtype', 'dtype_policy',
            'attention_implementation', 'cuda_device_name', 'torch_version',
            'torch_cuda_version', 'breeze_fast_graphs_frozen']
    return model, {k: runtime[k] for k in safe if k in runtime}


def verify_output_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path == REPO or REPO in path.parents:
        raise ValueError('benchmark artifacts must be outside the repository (no WAVs or private data in Git)')
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_run_dir(parent: Path) -> Path:
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory = parent / f'{tag}-breeze-longform-{secrets.token_hex(3)}'
    directory.mkdir(mode=0o700, exist_ok=False)
    return directory


def run(args: argparse.Namespace) -> Path:
    text = args.input.read_text(encoding='utf8')
    if not text.strip():
        raise ValueError('input text is empty')
    chosen = args.variant or DEFAULT_VARIANTS
    unknown = set(chosen) - set(VARIANTS)
    if unknown:
        raise ValueError(f'unknown variants: {sorted(unknown)}')
    limit = args.max_segments
    excerpt, variant_plans = shared_excerpt(text, {name: VARIANTS[name] for name in chosen}, limit)
    excerpt_sha = sha256(normalize(excerpt).encode('utf8'))
    excerpt_words = len(excerpt.split())
    source_words = len(text.split())
    if any(normalize(' '.join(p.text for p in plan)) != normalize(excerpt)
           for plan in variant_plans.values()):
        raise AssertionError('selected variants do not cover the identical excerpt')
    # Quoted, unset shell variables are otherwise parsed as an empty voice.
    # Catch this before the network probe or the creation of any run artifacts.
    validate_voice(args.voice, {})
    base = args.url.rstrip('/')
    model, runtime = discover(base, voice=args.voice)
    model = args.model or model
    ref_wav = getattr(args, 'reference_wav', None)
    ref_text = getattr(args, 'reference_transcript', None)
    if (ref_wav is None) != (ref_text is None):
        raise ValueError('reference identity requires both --reference-wav and --reference-transcript')
    reference_identity = None
    if ref_wav is not None:
        from prepare_breeze_reference import describe_pair
        reference_identity = describe_pair(ref_wav, ref_text)
    prompt_file = getattr(args, 'baseline_instruction_file', None)
    baseline_instruction = prompt_file.read_text(encoding='utf8').strip() if prompt_file else None
    if prompt_file and not baseline_instruction:
        raise ValueError('baseline instruction file is empty')
    for name in chosen:
        validate_breeze_cfg(VARIANTS[name]['cfg_scale'],
                            variant_instruction(name, baseline_instruction))
    out = make_run_dir(verify_output_dir(args.output_dir))
    (out / 'input.txt').write_text(text, encoding='utf8')
    (out / 'evaluated.txt').write_text(excerpt + '\n', encoding='utf8')
    for name, plan in variant_plans.items():
        atomic_json(out / name / 'plan.json',
                    dict(target_words=VARIANTS[name]['target_words'],
                         hard_max_words=VARIANTS[name]['max_words'],
                         shared_excerpt_sha256=excerpt_sha,
                         segments=[p.record() for p in plan]))
    manifest = dict(schema=VERSION, created_utc=datetime.now(timezone.utc).isoformat(),
                    source_sha256=sha256(text.encode('utf8')), model=model,
                    voice=args.voice, endpoint=base, runtime=runtime,
                    seed=args.seed, variants=chosen, reference_identity=reference_identity,
                    comparison=dict(mode='full' if limit is None else 'shared_sentence_prefix',
                                    source_words=source_words, evaluated_words=excerpt_words,
                                    evaluated_text_sha256=excerpt_sha,
                                    truncated_for_budget=excerpt_words < source_words,
                                    max_segments_per_variant=limit,
                                    segments_per_variant={name:len(plan) for name,plan in variant_plans.items()}),
                    baseline_instruction_sha256=(sha256(baseline_instruction.encode('utf8')) if baseline_instruction else None),
                    status='running',
                    note='WAV completeness requires listening/ASR review; no automatic coverage proof')
    atomic_json(out / 'manifest.json', manifest)
    rows = []
    joins = {}
    abort_reason = None
    for name in chosen:
        variant = VARIANTS[name]
        pieces = variant_plans[name]
        variant_rows = []
        for i, passage in enumerate(pieces):
            piece = passage.text
            folder = out / name / f'{i:03d}'
            folder.mkdir(parents=True, exist_ok=False)
            payload = dict(model=model, voice=args.voice, input=piece,
                           response_format='wav', cfg_scale=variant['cfg_scale'],
                           seed=args.seed)
            instruction = variant_instruction(name, baseline_instruction)
            if instruction is not None:
                payload['instruction'] = instruction
            atomic_json(folder / 'request.json', payload)
            row = dict(variant=name, segment=i, words=passage.words,
                       characters=len(piece), text_sha256=sha256(piece.encode('utf8')),
                       word_start=passage.word_start, word_end=passage.word_end,
                       boundary_before=passage.boundary_before,
                       forced_sentence_split=passage.forced_sentence_split,
                       status='error')
            start = time.perf_counter()
            try:
                wav, response = _request(base + '/v1/audio/speech', payload=payload, timeout=args.timeout)
                if not response['content_type'].lower().startswith(('audio/wav', 'audio/x-wav', 'application/octet-stream')):
                    raise ValueError(f'expected WAV but server returned {response["content_type"]}')
                measurements = inspect_wav_bytes(wav)
                # Never publish a binary before the structural check succeeds.
                (folder / 'audio.wav').write_bytes(wav)
                elapsed = time.perf_counter() - start
                measurements.update(wall_seconds=round(elapsed, 4),
                                    rtf=round(elapsed / measurements['duration_seconds'], 4),
                                    wav_sha256=sha256(wav),
                                    http_status=response['status'])
                atomic_json(folder / 'metrics.json', measurements)
                row.update(status='needs_listening_review', **measurements)
            except (OSError, RuntimeError, ValueError, wave.Error, urllib.error.URLError) as exc:
                row['error'] = f'{type(exc).__name__}: {exc}'[:600]
                row['error_category'] = failure_label(exc)
                if isinstance(exc, RequestFailure):
                    row['http_status'] = exc.status
                    # Fixed invalid requests are not useful to retry for each
                    # segment. Stop after retaining the first response.
                    if exc.status in (400, 401, 403, 404, 422):
                        abort_reason = row['error_category']
                atomic_json(folder / 'error.json', dict(error=row['error'],
                                                        category=row['error_category']))
            rows.append(row)
            variant_rows.append(row)
            atomic_json(out / 'results.json', rows)
            diagnostic = (' (' + row['error_category'] + ')' if 'error_category' in row else '')
            print(f'{name} {i+1}/{len(pieces)} {row["status"]}{diagnostic}', flush=True)
            if abort_reason:
                break
        if variant_rows and all(row['status'] == 'needs_listening_review' for row in variant_rows):
            preview = out / name / 'joined.wav'
            files = [out / name / f'{i:03d}' / 'audio.wav' for i in range(len(variant_rows))]
            try:
                paragraph_pause = getattr(args, 'paragraph_pause', None)
                if paragraph_pause is None:
                    paragraph_pause = args.join_pause * 2
                pauses = [(0.0 if segment.boundary_before == 'forced_sentence_split'
                           else paragraph_pause if segment.boundary_before == 'paragraph'
                           else args.join_pause)
                          for segment in pieces[1:]]
                joined = join_wavs(files, preview, pause_seconds=pauses)
                joined['shared_excerpt_sha256'] = excerpt_sha
                joined['join_boundary_types'] = [s.boundary_before for s in pieces[1:]]
                joined['wav_sha256'] = sha256(preview.read_bytes())
                atomic_json(out / name / 'joined.metrics.json', joined)
                joins[name] = dict(status='needs_listening_review', **joined)
                for idx, (pause, boundary) in enumerate(zip(pauses, pieces[1:]), start=1):
                    transition = out / name / f'join-{idx:03d}.wav'
                    metrics = boundary_preview(files[idx-1], files[idx], transition,
                                               pause_seconds=pause)
                    atomic_json(transition.with_suffix('.json'),
                                dict(from_segment=idx-1, to_segment=idx,
                                     boundary_type=boundary.boundary_before, **metrics))
            except (OSError, ValueError, wave.Error) as exc:
                preview.unlink(missing_ok=True)
                joins[name] = dict(status='error', error=f'{type(exc).__name__}: {exc}')
        else:
            joins[name] = dict(status='skipped_due_to_failed_segment')
        atomic_json(out / 'joins.json', joins)
        if abort_reason:
            break
    manifest['status'] = 'aborted' if abort_reason else 'recorded'
    if abort_reason:
        manifest['abort_reason'] = abort_reason
    manifest['completed_utc'] = datetime.now(timezone.utc).isoformat()
    manifest['segments'] = len(rows)
    manifest['join_pause_seconds'] = args.join_pause
    manifest['mechanically_valid'] = sum(r['status'] == 'needs_listening_review' for r in rows)
    manifest['errors'] = len(rows) - manifest['mechanically_valid']
    atomic_json(out / 'manifest.json', manifest)
    # Human review must explicitly approve each segment and each join.
    fields = ['variant', 'segment', 'word_start', 'word_end', 'boundary_before',
              'forced_sentence_split', 'completeness', 'voice_consistency',
              'naturalness', 'cadence_inside_sentence', 'boundary_transition',
              'math_pronunciation', 'joins', 'notes']
    lines = ['\t'.join(fields)]
    for row in rows:
        lines.append('\t'.join(str(row.get(k, '')) for k in fields))
    (out / 'LISTEN_REVIEW.tsv').write_text('\n'.join(lines) + '\n', encoding='utf8')
    join_review = ['\t'.join(['variant', 'from_segment', 'to_segment',
                                  'boundary_type', 'pause_seconds', 'join_preview',
                                  'cadence_continuity', 'voice_consistency', 'notes'])]
    for name in chosen:
        pieces = variant_plans[name]
        for index, passage in enumerate(pieces[1:], start=1):
            clip = f'{name}/join-{index:03d}.wav'
            if not (out / clip).exists():
                continue
            paragraph_pause = getattr(args, 'paragraph_pause', None)
            if paragraph_pause is None:
                paragraph_pause = args.join_pause * 2
            pause = (0.0 if passage.boundary_before == 'forced_sentence_split'
                     else paragraph_pause if passage.boundary_before == 'paragraph'
                     else args.join_pause)
            fields = [name, index-1, index, passage.boundary_before,
                      pause, clip, '', '', '']
            join_review.append('\t'.join(map(str, fields)))
    (out / 'JOIN_REVIEW.tsv').write_text('\n'.join(join_review) + '\n', encoding='utf8')
    return out


def _retained_error_category(row: dict) -> str:
    if row.get('error_category'):
        return row['error_category']
    # Older retained runs recorded the raw exception but no category. Parse
    # only status/type; never echo voice names, transcript text, or file paths.
    message = str(row.get('error', ''))
    status = re.search(r'\bHTTP (\d{3})\b', message)
    if status:
        return failure_label(RequestFailure(int(status.group(1)), message))
    if 'wave.Error' in message or 'not a WAV' in message:
        return 'invalid WAV returned by server'
    if 'URLError' in message or 'Connection refused' in message:
        return 'speech endpoint connection failed'
    if 'ValueError' in message:
        return 'invalid response or audio measurement'
    return 'error; inspect private error.json'


def report(run_directory: Path) -> dict:
    """Report existing error categories without showing private voice names."""
    directory = Path(run_directory).expanduser().resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
    rows = json.loads((directory / 'results.json').read_text(encoding='utf8'))
    counts = {}
    for row in rows:
        if row['status'] != 'error':
            continue
        category = _retained_error_category(row)
        counts[category] = counts.get(category, 0) + 1
    summary = dict(status=manifest.get('status'), segments=len(rows),
                   failures=sum(counts.values()), categories=counts,
                   hint='Inspect the run directory error.json privately for full server details')
    # V3 exposes coverage comparability without printing any voice or text.
    if isinstance(manifest.get('comparison'), dict):
        summary['comparison'] = manifest['comparison']
        by_variant = {}
        for name in manifest.get('variants', []):
            subset = [row for row in rows if row['variant'] == name]
            joined_path = directory / name / 'joined.metrics.json'
            joined = json.loads(joined_path.read_text()) if joined_path.is_file() else {}
            levels = [row['rms_dbfs'] for row in subset if row.get('rms_dbfs') is not None]
            by_variant[name] = dict(segments=len(subset),
                                    completed=sum(r['status']=='needs_listening_review' for r in subset),
                                    words=sum(r.get('words',0) for r in subset),
                                    joined_seconds=joined.get('duration_seconds'),
                                    rms_range_db=round(max(levels)-min(levels),3) if levels else None,
                                    forced_sentence_splits=sum(bool(r.get('forced_sentence_split')) for r in subset))
        summary['variants'] = by_variant
    return summary


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan', help='print deterministic passage sizes without contacting a server')
    plan.add_argument('--input', type=Path, default=DEFAULT_EXCERPT)
    plan.add_argument('--variant', action='append', choices=sorted(VARIANTS))
    plan.add_argument('--max-segments', type=int,
                      help='optional equal-text sentence-prefix cap, per variant')
    summary = sub.add_parser('report', help='summarize a previous run without speaker identifiers')
    summary.add_argument('--run-dir', required=True, type=Path)
    runp = sub.add_parser('run', help='retain a benchmark run against an existing Breeze endpoint')
    runp.add_argument('--url', required=True, help='server base URL, without /v1')
    runp.add_argument('--voice', required=True, help='name of an authorized voice installed on the server')
    runp.add_argument('--model', help='override /v1/models discovery')
    runp.add_argument('--reference-wav', type=Path,
                      help='local reference WAV, used only for private digest/quality metadata')
    runp.add_argument('--reference-transcript', type=Path,
                      help='exact matching reference transcript (requires --reference-wav)')
    runp.add_argument('--baseline-instruction-file', type=Path,
                      help='private delivery instruction for short12 and directed40 variants')
    runp.add_argument('--input', type=Path, default=DEFAULT_EXCERPT)
    runp.add_argument('--output-dir', type=Path, required=True,
                      help='artifact parent outside this Git repository')
    runp.add_argument('--variant', action='append', choices=sorted(VARIANTS))
    runp.add_argument('--max-segments', type=int, default=None,
                      help='optional request budget per variant; compare the same complete-sentence prefix')
    runp.add_argument('--timeout', type=float, default=300)
    runp.add_argument('--join-pause', type=float, default=0.15, help='sentence-join preview pause, seconds')
    runp.add_argument('--paragraph-pause', type=float, default=0.30,
                      help='paragraph-join preview pause, seconds')
    runp.add_argument('--seed', type=int, default=42)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.command == 'plan':
        text = args.input.read_text(encoding='utf8')
        chosen = args.variant or DEFAULT_VARIANTS
        excerpt, plans = shared_excerpt(text, {name: VARIANTS[name] for name in chosen},
                                        args.max_segments)
        summary = dict(source_words=len(text.split()), evaluated_words=len(excerpt.split()),
                       evaluated_text_sha256=sha256(normalize(excerpt).encode()),
                       full_coverage=len(excerpt.split()) == len(text.split()),
                       max_segments_per_variant=args.max_segments)
        rows = {key: [dict(words=p.words, characters=len(p.text),
                            word_start=p.word_start, word_end=p.word_end,
                            boundary_before=p.boundary_before,
                            forced_sentence_split=p.forced_sentence_split,
                            text_sha256=sha256(p.text.encode())) for p in segments]
                for key, segments in plans.items()}
        print(json.dumps(dict(comparison=summary, variants=rows), indent=2))
        return 0
    if args.command == 'report':
        print(json.dumps(report(args.run_dir), indent=2))
        return 0
    output = run(args)
    print(f'Retained benchmark: {output}')
    summary = report(output)
    if summary['failures']:
        print('ERROR: benchmark had synthesis failures; use the report command or '
              'inspect private error.json files for details', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
