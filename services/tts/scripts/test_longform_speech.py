"""Offline paragraph80 synthesis tests; no model, GPU, or private voice assets."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request
import wave

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from longform_speech import POLICY, _wav_pcm, synthesize_longform


def wav_for_words(text: str, *, rate: int = 8000, seconds_per_word: float = .14) -> bytes:
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\x00\x00' * round(rate * max(0.15, len(text.split()) * seconds_per_word)))
    return raw.getvalue()


class ParagraphSpeechTests(unittest.TestCase):
    def test_sentence_and_paragraph_joins_preserve_coverage(self):
        paragraph1 = 'The residual controls the angle between two invariant subspaces. ' * 3
        paragraph2 = 'The spectral gap gives the estimate used in the proof. ' * 3
        text = paragraph1.strip() + '\n\n' + paragraph2.strip()
        parts = []
        result = synthesize_longform(text, lambda piece: (parts.append(piece), wav_for_words(piece))[1])
        self.assertEqual(sum(len(x.split()) for x in parts), len(text.split()))
        self.assertEqual(result.timings['policy'], POLICY)
        self.assertEqual(sum(x['words'] for x in result.timings['chunks']), len(text.split()))
        self.assertEqual(len(result.timings['chunks']), len(parts))
        rate, pcm, seconds = _wav_pcm(result.audio)
        self.assertEqual(rate, 8000)
        self.assertAlmostEqual(seconds, result.timings['duration'], delta=.002)
        self.assertTrue(all(x.endswith('.') for x in parts))

    def test_forced_duration_fallback_preserves_every_word(self):
        text = 'One complete mathematical sentence is spoken before the next one begins. ' * 10
        calls = []

        def capped(piece):
            calls.append(len(piece.split()))
            if len(piece.split()) > 22:
                return wav_for_words(piece, seconds_per_word=1)
            return wav_for_words(piece)

        result = synthesize_longform(text, capped, max_segment_seconds=40)
        self.assertGreater(len(calls), len(result.timings['chunks']))
        self.assertEqual(sum(item['words'] for item in result.timings['chunks']), len(text.split()))
        self.assertTrue(all(item['words'] <= 22 for item in result.timings['chunks']))

    def test_short_response_is_not_published(self):
        text = 'A theorem depends on the assumptions needed to establish the operator bound. ' * 5
        with self.assertRaisesRegex(RuntimeError, 'implausible audio'):
            synthesize_longform(text, lambda piece: wav_for_words(piece, seconds_per_word=.005))

    def test_reject_bad_input(self):
        for invalid in ('', '  ', 'a ' * 451):
            with self.assertRaises(ValueError):
                synthesize_longform(invalid, wav_for_words)

    def test_http_opt_in_uses_cfg_one_without_instruction(self):
        script = HERE / 'candidate_server.py'
        spec = importlib.util.spec_from_file_location('longform_candidate_test', script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        module.BACKEND = 'breeze-tts2'

        class StubRuntime:
            def __init__(self):
                self.requests = []

            def synthesize(self, payload):
                self.requests.append(payload)
                return wav_for_words(payload['input'])
        runtime = StubRuntime()
        module.RUNTIME = runtime
        server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/v1/audio/speech'
            body = {'model': 'breeze-tts2', 'voice': 'foobar',
                    'input': 'A complete sentence provides context for speech. ' * 20,
                    'cfg_scale': 1.0, 'response_format': 'wav', 'long_form': True}

            def send(payload):
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={'Content-Type': 'application/json'})
                return urllib.request.urlopen(req, timeout=5)

            with send(body) as response:
                self.assertEqual(response.headers['X-Local-AI-Speech-Policy'], POLICY)
                timing = json.loads(response.headers['X-Local-AI-Speech-Timings'])
                self.assertEqual(sum(c['words'] for c in timing['chunks']), len(body['input'].split()))
                self.assertGreater(len(timing['chunks']), 1)
                self.assertGreater(_wav_pcm(response.read())[2], 0)
            self.assertTrue(all(r['cfg_scale'] == 1.0 and 'instruction' not in r
                                for r in runtime.requests))
            # Without long_form, retain the old one-shot API semantics.
            single = {**body, 'input': 'One complete sentence.',
                      'long_form': False, 'instruction': 'Speak plainly.', 'cfg_scale': 2.0}
            with send(single) as response:
                self.assertIsNone(response.headers.get('X-Local-AI-Speech-Policy'))
                self.assertGreater(_wav_pcm(response.read())[2], 0)
            self.assertEqual(runtime.requests[-1]['instruction'], 'Speak plainly.')
            self.assertEqual(runtime.requests[-1]['cfg_scale'], 2.0)
            prior = len(runtime.requests)
            for invalid in ({**body, 'cfg_scale': 2.0},
                            {**body, 'instruction': 'Be theatrical.'},
                            {**body, 'long_form': 'true'}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    send(invalid)
                self.assertEqual(error.exception.code, 400)
            self.assertEqual(len(runtime.requests), prior)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
