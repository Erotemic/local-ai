"""Offline tests for server-side job queue, progress, and cancellation."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from speech_jobs import SpeechJobQueue
from test_longform_speech import wav_for_words

TEXT = ('The residual controls the change in the invariant subspace. '
        'The spectral gap bounds its magnitude. ') * 10


def wait_for(condition, *, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(.01)
    raise AssertionError('job did not reach expected state')


class SpeechJobTests(unittest.TestCase):
    def body(self):
        return dict(model='breeze-tts2', voice='foobar', input=TEXT,
                    response_format='wav', cfg_scale=1, long_form=True)

    def test_job_progress_audio_and_redacted_status(self):
        first = threading.Event()
        release = threading.Event()
        observed = []
        def synth(piece, body):
            observed.append(piece)
            first.set()
            release.wait(2)
            return wav_for_words(piece)
        jobs = SpeechJobQueue(synth)
        try:
            row = jobs.submit(self.body())
            self.assertIn(row['state'], ('queued', 'rendering'))
            wait_for(first.is_set)
            active = jobs.status(row['id'])
            self.assertEqual(active['phase'], 'synthesizing')
            self.assertEqual(active['current_passage'], 1)
            self.assertGreater(active['current_passage_words'], 0)
            self.assertEqual(active['completed_passages'], 0)
            self.assertEqual(active['total_words'], len(TEXT.split()))
            self.assertNotIn('foobar', json.dumps(jobs.status(row['id'])))
            release.set()
            wait_for(lambda: jobs.status(row['id'])['state'] == 'ready')
            finished = jobs.status(row['id'])
            self.assertEqual(finished['completed_words'], len(TEXT.split()))
            self.assertGreater(finished['completed_passages'], 1)
            result = jobs.audio(row['id'])
            self.assertIsNotNone(result)
            self.assertEqual(sum(item['words'] for item in result[1]['chunks']), len(TEXT.split()))
        finally:
            release.set()
            jobs.close()

    def test_priority_and_cancel_queued(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def synth(piece, body):
            calls.append(body['marker'])
            if body['marker'] == 'active':
                entered.set()
                release.wait(3)
            return wav_for_words(piece)
        jobs = SpeechJobQueue(synth, workers=1)
        try:
            active = jobs.submit({**self.body(), 'marker': 'active'})
            wait_for(entered.is_set)
            low = jobs.submit({**self.body(), 'marker': 'low'}, priority=20)
            high = jobs.submit({**self.body(), 'marker': 'high'}, priority=0)
            stopped = jobs.submit({**self.body(), 'marker': 'stopped'}, priority=10)
            self.assertEqual(jobs.cancel(stopped['id'])['state'], 'cancelled')
            release.set()
            wait_for(lambda: jobs.status(low['id'])['state'] == 'ready', timeout=8)
            self.assertLess(calls.index('high'), calls.index('low'))
            self.assertNotIn('stopped', calls)
        finally:
            release.set()
            jobs.close()

    def test_active_cancel_stops_between_passages(self):
        first = threading.Event()
        release = threading.Event()
        count = []
        def synth(piece, body):
            count.append(piece)
            first.set()
            release.wait(2)
            return wav_for_words(piece)
        jobs = SpeechJobQueue(synth)
        try:
            row = jobs.submit(self.body())
            wait_for(first.is_set)
            self.assertEqual(jobs.cancel(row['id'])['state'], 'canceling')
            release.set()
            wait_for(lambda: jobs.status(row['id'])['state'] == 'cancelled')
            self.assertEqual(len(count), 1)
            self.assertIsNone(jobs.audio(row['id']))
        finally:
            release.set()
            jobs.close()

    def test_http_lifecycle(self):
        spec = importlib.util.spec_from_file_location('candidate_speech_jobs_test', HERE/'candidate_server.py')
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        module.BACKEND = 'breeze-tts2'
        class StubRuntime:
            def synthesize(self, body):
                return wav_for_words(body['input'])
            def metadata(self):
                return {'backend': 'breeze-tts2'}
        module.RUNTIME = StubRuntime()
        module.JOB_QUEUE = SpeechJobQueue(lambda piece, body: module.RUNTIME.synthesize({**body, 'input':piece}))
        server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            request = urllib.request.Request(base+'/v1/audio/jobs', data=json.dumps(self.body()).encode(),
                                             headers={'Content-Type':'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=4) as response:
                self.assertEqual(response.status, 202)
                created = json.load(response)
            job_url = base + '/v1/audio/jobs/' + created['id']
            def ready():
                with urllib.request.urlopen(job_url, timeout=3) as response:
                    return json.load(response)['state'] == 'ready'
            wait_for(ready, timeout=5)
            with urllib.request.urlopen(job_url+'/audio', timeout=3) as response:
                self.assertEqual(response.headers['X-Local-AI-Speech-Policy'], 'paragraph80-v1')
                self.assertTrue(response.read().startswith(b'RIFF'))
            with urllib.request.urlopen(base+'/v1/runtime', timeout=3) as response:
                self.assertEqual(json.load(response)['long_form_jobs']['workers'], 1)
            self.assertEqual(module.JOB_QUEUE.cancel(created['id'])['state'], 'ready')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base+'/v1/audio/jobs/unknown', timeout=2)
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            module.JOB_QUEUE.close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
