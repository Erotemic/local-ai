"""Server-side Breeze paragraph narration: complete sentences, CFG 1, PCM joins.

Only opt-in requests use this policy. The one-shot speech endpoint retains its
previous behavior. This module has no torch dependency and can be tested offline.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
import math
import os
import wave
from typing import Callable

from passage_planner import Passage, plan_passages

POLICY = 'paragraph80-v1'
TARGET_WORDS = 80
HARD_MAX_WORDS = 110
MAX_INPUT_CHARS = 9000
MAX_INPUT_WORDS = 450


class JobCancelled(Exception):
    """Cooperative cancellation between synthesis passages."""


@dataclass(frozen=True)
class LongformResult:
    audio: bytes
    timings: dict


def _wav_pcm(data: bytes) -> tuple[int, bytes, float]:
    try:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
            frames = wav.getnframes()
            pcm = wav.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise RuntimeError(f'Breeze produced an invalid WAV: {error}') from error
    if channels != 1 or width != 2 or rate <= 0 or frames <= 0:
        raise RuntimeError('Breeze must return nonempty mono PCM16 audio')
    if len(pcm) != frames * 2:
        raise RuntimeError('Breeze returned incomplete PCM audio')
    return rate, pcm, frames / rate


def synthesize_longform(text: str, synthesis: Callable[[str], bytes], *,
                        max_segment_seconds: float | None = None,
                        max_words_per_second: float = 8.0,
                        sentence_pause: float = 0.15,
                        paragraph_pause: float = 0.30,
                        progress: Callable[[int, int], None] | None = None,
                        passage_started: Callable[[int, int], None] | None = None,
                        cancelled: Callable[[], bool] | None = None) -> LongformResult:
    """Synthesize every passage, shortening ones that fail duration safeguards.

    This does not assert word-perfect output: duration checks only detect likely
    generation cutoffs or implausibly brief speech. The model never receives
    the preceding passage as additional spoken input.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError('long_form input must be nonempty text')
    if len(text) > MAX_INPUT_CHARS or len(text.split()) > MAX_INPUT_WORDS:
        raise ValueError('long_form input exceeds the page-sized request limit')
    if max_segment_seconds is None:
        max_segment_seconds = float(os.environ.get('TTS_BREEZE_LONGFORM_MAX_SECONDS', '45'))
    if not 1 <= max_segment_seconds <= 120 or not math.isfinite(max_segment_seconds):
        raise ValueError('long-form max segment seconds must be between 1 and 120')
    if max_words_per_second < 0 or not math.isfinite(max_words_per_second):
        raise ValueError('invalid max words per second')
    if not (0 <= sentence_pause <= 3 and 0 <= paragraph_pause <= 3):
        raise ValueError('invalid long-form pause configuration')

    planned = plan_passages(text, target_words=TARGET_WORDS, hard_max_words=HARD_MAX_WORDS)
    accepted: list[tuple[Passage, int, bytes, float]] = []

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise JobCancelled('Generation cancelled')

    def generate(passage: Passage, depth: int = 0) -> None:
        check_cancelled()
        if depth > 12:
            raise RuntimeError('Breeze exceeded its generation limit after repeated splits')
        if passage_started is not None:
            passage_started(len(accepted) + 1, passage.words)
        rate, pcm, seconds = _wav_pcm(synthesis(passage.text))
        check_cancelled()
        excessive = seconds >= max_segment_seconds
        implausibly_short = (max_words_per_second > 0 and passage.words >= 4
                              and seconds * max_words_per_second < passage.words)
        if excessive or implausibly_short:
            if passage.words <= 8:
                raise RuntimeError('Breeze returned implausible audio even for a short passage')
            # First try smaller *complete sentences*. Split the sentence itself
            # only if it cannot fit the tighter duration budget.
            smaller_max = max(8, min(passage.words // 2 + 1, HARD_MAX_WORDS // 2))
            sub = plan_passages(passage.text, target_words=max(2, smaller_max - 1),
                                hard_max_words=smaller_max)
            if len(sub) < 2:
                raise RuntimeError('Unable to shorten Breeze passage')
            for i, part in enumerate(sub):
                if i == 0:
                    boundary = passage.boundary_before
                else:
                    boundary = part.boundary_before
                # A new local plan cannot recover the global paragraph indices.
                part = Passage(part.text, part.words, part.word_start, part.word_end,
                               boundary, part.paragraph_start, part.paragraph_end,
                               part.forced_sentence_split)
                generate(part, depth + 1)
            return
        accepted.append((passage, rate, pcm, seconds))
        if progress is not None:
            progress(sum(item[0].words for item in accepted), len(accepted))

    for passage in planned:
        generate(passage)

    check_cancelled()
    rate = accepted[0][1]
    if any(item[1] != rate for item in accepted):
        raise RuntimeError('Breeze changed sample rate between long-form passages')
    pcm_pieces: list[bytes] = []
    timings: list[dict] = []
    cursor_frames = 0
    for i, (passage, _, pcm, _) in enumerate(accepted):
        if i:
            pause = (paragraph_pause if passage.boundary_before == 'paragraph'
                     else 0.0 if passage.boundary_before == 'forced_sentence_split'
                     else sentence_pause)
            count = round(rate * pause)
            pcm_pieces.append(b'\x00\x00' * count)
            cursor_frames += count
        start = cursor_frames
        pcm_pieces.append(pcm)
        cursor_frames += len(pcm) // 2
        timings.append({'words': passage.words,
                        'start': round(start / rate, 5),
                        'end': round(cursor_frames / rate, 5),
                        'boundary_before': passage.boundary_before})
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b''.join(pcm_pieces))
    info = {'version': 1, 'policy': POLICY, 'duration': round(cursor_frames / rate, 5),
            'chunks': timings}
    return LongformResult(output.getvalue(), info)


def timing_header(data: dict) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(',', ':'))
