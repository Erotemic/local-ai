"""Bounded, priority-ordered long-form TTS jobs with cooperative cancellation.

Job bodies and generated audio live in process memory, not the repository. Job
status intentionally excludes speaker names, reference paths, and input text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
import threading
import time
from uuid import uuid4

from longform_speech import POLICY, TARGET_WORDS, HARD_MAX_WORDS, JobCancelled, synthesize_longform
from passage_planner import plan_passages



@dataclass
class SpeechJob:
    id: str
    body: dict
    priority: int
    submitted_at: float = field(default_factory=time.monotonic)
    state: str = 'queued'
    started_at: float | None = None
    finished_at: float | None = None
    completed_words: int = 0
    total_words: int = 0
    completed_passages: int = 0
    total_passages: int = 0
    current_passage: int = 0
    current_passage_words: int = 0
    phase: str = 'queued'
    error: str | None = None
    audio: bytes | None = None
    timings: dict | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def public(self, queued_position=None):
        now = time.monotonic()
        elapsed = max(0., (self.finished_at or now) - self.started_at) if self.started_at else 0.
        result = dict(id=self.id, state=self.state, policy=POLICY,
                      elapsed_seconds=round(elapsed, 2),
                      completed_words=self.completed_words, total_words=self.total_words,
                      completed_passages=self.completed_passages, total_passages=self.total_passages,
                      current_passage=self.current_passage,
                      current_passage_words=self.current_passage_words, phase=self.phase)
        if queued_position is not None:
            result['queued_position'] = queued_position
        if self.error:
            result['error'] = self.error
        return result


class SpeechJobQueue:
    def __init__(self, synthesize, *, workers=1, max_waiting=12, retention_seconds=900):
        if workers < 1 or max_waiting < 1:
            raise ValueError('invalid queue capacity')
        self.synthesize = synthesize
        self.max_waiting = max_waiting
        self.retention_seconds = retention_seconds
        self.condition = threading.Condition()
        self.jobs: dict[str, SpeechJob] = {}
        self.heap = []
        self.sequence = 0
        self.stopped = False
        self.threads = [threading.Thread(target=self._worker, daemon=True, name=f'breeze-job-{n}')
                        for n in range(workers)]
        for thread in self.threads:
            thread.start()

    def _prune(self):
        now = time.monotonic()
        for key, job in list(self.jobs.items()):
            if job.finished_at is not None and now - job.finished_at > self.retention_seconds:
                self.jobs.pop(key, None)

    def submit(self, body: dict, priority=10) -> dict:
        if not isinstance(body, dict) or body.get('long_form') is not True:
            raise ValueError('job submissions require long_form=true')
        if any(body.get(key) for key in ('instruction', 'direction', 'voice_direction')):
            raise ValueError('long-form jobs do not accept speaking directions')
        if float(body.get('cfg_scale', 1)) != 1.0:
            raise ValueError('long-form jobs require cfg_scale=1.0')
        text = body.get('input')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('job input must be nonempty text')
        from longform_speech import MAX_INPUT_CHARS, MAX_INPUT_WORDS
        if len(text) > MAX_INPUT_CHARS or len(text.split()) > MAX_INPUT_WORDS:
            raise ValueError('job input exceeds page limit')
        if body.get('response_format', 'wav') not in ('wav', 'wave'):
            raise ValueError('long-form jobs currently require WAV output')
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 20:
            raise ValueError('job priority must be an integer between 0 and 20')
        planned = plan_passages(text, target_words=TARGET_WORDS, hard_max_words=HARD_MAX_WORDS)
        with self.condition:
            self._prune()
            waiting = sum(job.state == 'queued' for job in self.jobs.values())
            if waiting >= self.max_waiting:
                raise OverflowError('Breeze job queue is full')
            job = SpeechJob(uuid4().hex, dict(body), priority,
                            total_words=len(text.split()), total_passages=len(planned))
            self.jobs[job.id] = job
            self.sequence += 1
            heapq.heappush(self.heap, (priority, self.sequence, job.id))
            self.condition.notify()
            return self._status_locked(job)

    def _status_locked(self, job):
        if job.state != 'queued':
            return job.public()
        queued = sorted((j.priority, j.submitted_at, j.id) for j in self.jobs.values()
                        if j.state == 'queued')
        position = next((i + 1 for i, (_, _, key) in enumerate(queued) if key == job.id), None)
        return job.public(queued_position=position)

    def status(self, job_id):
        with self.condition:
            self._prune()
            job = self.jobs.get(job_id)
            return self._status_locked(job) if job else None

    def audio(self, job_id):
        with self.condition:
            job = self.jobs.get(job_id)
            if job and job.state == 'ready':
                return job.audio, job.timings
            return None

    def cancel(self, job_id):
        with self.condition:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.state in ('queued', 'rendering', 'canceling'):
                job.cancel.set()
                if job.state == 'queued':
                    job.state = 'cancelled'
                    job.finished_at = time.monotonic()
                    job.phase = 'finished'
                elif job.state == 'rendering':
                    job.state = 'canceling'
            self.condition.notify_all()
            return self._status_locked(job)

    def _worker(self):
        while True:
            with self.condition:
                while not self.heap and not self.stopped:
                    self.condition.wait()
                if self.stopped:
                    return
                _, _, key = heapq.heappop(self.heap)
                job = self.jobs.get(key)
                if not job or job.state != 'queued':
                    continue
                job.state = 'rendering'
                job.started_at = time.monotonic()
                job.phase = 'starting'
            try:
                def progress(words, passages):
                    with self.condition:
                        job.completed_words = words
                        job.completed_passages = passages
                        job.total_passages = max(job.total_passages, passages)
                        if job.cancel.is_set():
                            raise JobCancelled('Generation cancelled')

                def passage_started(index, words):
                    with self.condition:
                        job.current_passage = index
                        job.current_passage_words = words
                        job.phase = 'synthesizing'

                def checked_synthesis(piece):
                    if job.cancel.is_set():
                        raise JobCancelled('Generation cancelled')
                    result = self.synthesize(piece, job.body)
                    if job.cancel.is_set():
                        raise JobCancelled('Generation cancelled')
                    return result

                result = synthesize_longform(job.body['input'], checked_synthesis,
                                             progress=progress, passage_started=passage_started,
                                             cancelled=job.cancel.is_set)
                with self.condition:
                    if job.cancel.is_set():
                        job.state = 'cancelled'
                    else:
                        job.audio = result.audio
                        job.timings = result.timings
                        job.completed_words = job.total_words
                        job.completed_passages = len(result.timings['chunks'])
                        job.state = 'ready'
                        job.phase = 'finished'
            except JobCancelled:
                with self.condition:
                    job.state = 'cancelled'
            except Exception as exc:
                with self.condition:
                    job.state = 'cancelled' if job.cancel.is_set() else 'error'
                    if job.state == 'error':
                        job.error = f'{type(exc).__name__}: {exc}'[:350]
            finally:
                with self.condition:
                    job.finished_at = time.monotonic()
                    job.phase = 'finished'
                    self.condition.notify_all()

    def close(self):
        with self.condition:
            self.stopped = True
            for job in self.jobs.values():
                if job.state in ('queued', 'rendering', 'canceling'):
                    job.cancel.set()
            self.condition.notify_all()
        for thread in self.threads:
            thread.join(timeout=5)
