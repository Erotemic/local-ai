"""Pure, offline sentence-preserving passage planner for long-form speech.

Target word counts are soft goals. Hard limits are applied only to unusually
long sentences (or groups of complete sentences). Nothing here invokes TTS;
in particular, contextual metadata is not included in the text to be spoken.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re

# Covers common prose abbreviations. This is a conservative boundary detector,
# not a language-dependent sentence tokenizer or a pronunciation subsystem.
ABBREVIATIONS = frozenset({
    'dr.', 'mr.', 'mrs.', 'ms.', 'prof.', 'fig.', 'eq.', 'sec.', 'e.g.',
    'i.e.', 'etc.', 'vs.', 'cf.', 'no.', 'st.',
})
TERMINAL = re.compile(r"[.!?][\"'\u201d\u2019)\]]*(?:\s+|$)")


def normalize(text: str) -> str:
    return ' '.join(text.split())


def sentences(text: str) -> list[str]:
    """Split on likely complete sentence endings without discarding text."""
    normalized = normalize(text)
    if not normalized:
        raise ValueError('text is empty')
    parts: list[str] = []
    start = 0
    for match in TERMINAL.finditer(normalized):
        leading = normalized[start:match.start()+1]
        # Search the final token, not an earlier abbreviation in the clause.
        previous = leading.split()[-1] if leading.split() else ''
        prev = previous.lower()
        if prev in ABBREVIATIONS or re.fullmatch(r'[A-Z]\.', previous):
            continue
        # A decimal point between digits is not a sentence delimiter.
        if match.start() > 0 and match.start()+1 < len(normalized):
            if normalized[match.start()-1].isdigit() and normalized[match.start()+1].isdigit():
                continue
        end = match.start() + len(match.group().rstrip())
        fragment = normalized[start:end].strip()
        if fragment:
            parts.append(fragment)
        start = match.end()
    if normalized[start:].strip():
        parts.append(normalized[start:].strip())
    if normalize(' '.join(parts)) != normalized:
        raise AssertionError('sentence segmentation changed input words')
    return parts


@dataclass(frozen=True)
class Unit:
    text: str
    words: int
    paragraph: int
    sentence: int
    forced_split: bool = False


@dataclass(frozen=True)
class Passage:
    text: str
    words: int
    word_start: int
    word_end: int
    boundary_before: str
    paragraph_start: int
    paragraph_end: int
    forced_sentence_split: bool

    def record(self) -> dict:
        return asdict(self)


def _units(text: str, hard_max_words: int) -> list[Unit]:
    units: list[Unit] = []
    sentence_index = 0
    for paragraph_index, paragraph in enumerate(p for p in re.split(r'\n\s*\n', text) if p.strip()):
        for sentence in sentences(paragraph):
            words = sentence.split()
            # Evenly distribute an overlong sentence to avoid single-word
            # tail requests (e.g. 81 words with an 80-word hard maximum).
            nchunks = math.ceil(len(words) / hard_max_words)
            base, extra = divmod(len(words), nchunks)
            pos = 0
            for idx in range(nchunks):
                size = base + (idx < extra)
                units.append(Unit(' '.join(words[pos:pos+size]), size,
                                  paragraph_index, sentence_index, idx > 0))
                pos += size
            sentence_index += 1
    return units


def plan_passages(text: str, *, target_words: int, hard_max_words: int,
                  minimum_words: int = 8) -> list[Passage]:
    """Group intact sentences with balanced lengths and paragraph preference.

    Dynamic programming chooses cuts between complete sentences and favors
    passages near target_words, penalizing very short isolated tails and
    joining paragraphs. A sentence longer than hard_max_words is balanced
    into word-boundary pieces before packing. Every source word is retained.
    """
    if not (2 <= target_words <= hard_max_words <= 1000):
        raise ValueError('require 2 <= target_words <= hard_max_words <= 1000')
    if minimum_words < 1:
        raise ValueError('minimum_words must be positive')
    units = _units(text, hard_max_words)
    if not units:
        raise ValueError('text is empty')
    n = len(units)
    cost = [float('inf')] * (n + 1)
    segments = [0] * (n + 1)
    next_cut = [-1] * n
    cost[n] = 0.0
    for i in range(n-1, -1, -1):
        words, paragraph_crossings = 0, 0
        for j in range(i, n):
            words += units[j].words
            if words > hard_max_words:
                break
            if j > i and units[j].paragraph != units[j-1].paragraph:
                paragraph_crossings += 1
            # Equivalent groupings prefer fewer very short tails and fewer
            # paragraph crossings; the target itself is not a hard cutoff.
            short = max(0, min(minimum_words, target_words//3) - words)
            local_cost = ((words - target_words) ** 2
                          + 3.0 * short**2
                          + paragraph_crossings * 0.55 * target_words**2
                          + 0.10 * target_words**2)
            candidate = local_cost + cost[j+1]
            candidate_count = 1 + segments[j+1]
            if (candidate < cost[i] - 1e-9 or
                (abs(candidate - cost[i]) <= 1e-9 and candidate_count < segments[i])):
                cost[i] = candidate
                segments[i] = candidate_count
                next_cut[i] = j + 1
    if next_cut[0] == -1:
        raise ValueError('cannot construct speech passages under the hard word limit')
    passages: list[Passage] = []
    i, word_cursor = 0, 0
    while i < n:
        j = next_cut[i]
        chunk = units[i:j]
        text_parts = [chunk[0].text]
        for prior, current in zip(chunk, chunk[1:]):
            join = '\n\n' if prior.paragraph != current.paragraph else ' '
            text_parts.append(join + current.text)
        words = sum(u.words for u in chunk)
        if i == 0:
            boundary = 'start'
        elif units[i].sentence == units[i-1].sentence:
            boundary = 'forced_sentence_split'
        elif units[i].paragraph != units[i-1].paragraph:
            boundary = 'paragraph'
        else:
            boundary = 'sentence'
        passages.append(Passage(''.join(text_parts), words, word_cursor,
                                word_cursor+words, boundary,
                                chunk[0].paragraph, chunk[-1].paragraph,
                                any(u.forced_split for u in chunk) or boundary == 'forced_sentence_split'))
        word_cursor += words
        i = j
    if normalize(' '.join(p.text for p in passages)) != normalize(text):
        raise AssertionError('passage plan lost, duplicated, or reordered text')
    return passages


def shared_excerpt(text: str, configurations: dict[str, dict],
                   max_segments: int | None) -> tuple[str, dict[str, list[Passage]]]:
    """Choose one sentence-complete prefix fitting every variant's budget.

    No cap means all source text is compared. When max_segments is supplied,
    it limits the number of requests for *each* variant, not the text each
    variant independently selects. Never cut the common excerpt mid-sentence.
    """
    if max_segments is not None and max_segments < 1:
        raise ValueError('--max-segments must be positive')

    def build(excerpt: str) -> dict[str, list[Passage]]:
        return {name: plan_passages(excerpt,
                                   target_words=spec['target_words'],
                                   hard_max_words=spec['max_words'])
                for name, spec in configurations.items()}

    if max_segments is None:
        excerpt = text.strip()
        if not excerpt:
            raise ValueError('input text is empty')
        return excerpt, build(excerpt)

    # Construct sentence-complete prefixes, retaining paragraph separators.
    paragraphs = [sentences(p) for p in re.split(r'\n\s*\n', text) if p.strip()]
    groups: list[list[str]] = []
    best_text: str | None = None
    best_plan: dict[str, list[Passage]] | None = None
    strictest_budget = max_segments * min(spec['max_words'] for spec in configurations.values())
    word_total = 0
    for paragraph in paragraphs:
        groups.append([])
        for sentence in paragraph:
            groups[-1].append(sentence)
            word_total += len(sentence.split())
            # A prefix larger than the smallest variant's total hard budget
            # can never fit later, so avoid replanning an entire long lecture.
            if word_total > strictest_budget:
                if best_text is None or best_plan is None:
                    raise ValueError('--max-segments cannot fit the first complete sentence '
                                     'under every selected variant; increase the budget')
                return best_text, best_plan
            candidate = '\n\n'.join(' '.join(group) for group in groups)
            plans = build(candidate)
            if all(len(items) <= max_segments for items in plans.values()):
                best_text, best_plan = candidate, plans
    if best_text is None or best_plan is None:
        raise ValueError('--max-segments cannot fit the first complete sentence '
                         'under every selected variant; increase the budget')
    return best_text, best_plan
