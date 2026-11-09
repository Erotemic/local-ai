#!/usr/bin/env python3
"""No-GPU tests for retained Breeze experiments and private reference setup."""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import wave

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import longform_benchmark as bench
import prepare_breeze_reference as reference


def fake_wav(seconds: float = 1.4) -> bytes:
    rate = 8000
    content = b''.join(struct.pack('<h', int(800 * math.sin(2*math.pi*240*i/rate)))
                       for i in range(int(seconds*rate)))
    target = io.BytesIO()
    with wave.open(target, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(content)
    return target.getvalue()


class StubHandler(BaseHTTPRequestHandler):
    requests = []
    clipped = False

    def log_message(self, fmt, *args):
        pass

    def reply(self, data, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/v1/models':
            body = {'data': [{'id': 'breeze-tts2'}]}
        elif self.path == '/v1/runtime':
            body = {'backend': 'breeze-tts2', 'model_revision': 'example-pin',
                    'breeze_fast': True, 'model_dir': '/private/not-for-export',
                    'reference_wav': '/voices/secret.wav'}
        else:
            self.send_error(404)
            return
        self.reply(json.dumps(body).encode(), 'application/json')

    def do_POST(self):
        if self.path != '/v1/audio/speech':
            self.send_error(404)
            return
        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(req)
        self.reply(b'not a WAV' if self.clipped else fake_wav(), 'audio/wav')


class LongformTests(unittest.TestCase):
    def test_sentence_splitting_and_passage_coverage(self):
        text = 'Dr. Brown studies matrices. The eigenspaces are stable. \'A spectral gap helps.\' Next we compare them!'
        self.assertEqual(' '.join(bench.sentences(text)), text)
        for size in [12, 25, 40]:
            chunks = bench.passages(text, size)
            self.assertEqual(' '.join(chunks), text)
            self.assertTrue(all(len(c.split()) <= size for c in chunks))
        self.assertRaises(ValueError, bench.passages, 'short', 1)
        self.assertRaises(ValueError, bench.sentences, '')
        self.assertEqual(bench.passages('One complete paragraph.\n\nAnother complete paragraph.', 40),
                         ['One complete paragraph.', 'Another complete paragraph.'])

    def test_reference_inspection_and_private_pair_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / 'source.wav'
            transcript = root / 'source.txt'
            audio.write_bytes(fake_wav())
            transcript.write_text('A careful sentence provides a reference.', encoding='utf8')
            meta = reference.describe_pair(audio, transcript)
            self.assertEqual(meta['duration_seconds'], 1.4)
            self.assertEqual(meta['transcript_words'], 6)
            self.assertNotIn('A careful sentence', json.dumps(meta))
            result = reference.prepare(argparse.Namespace(
                wav=audio, transcript=transcript, selected_transcript=None,
                output_dir=root / 'voices', name='foobar', start=None, end=None,
                normalize=False, force=False))
            self.assertEqual((root / 'voices' / 'foobar.wav').read_bytes(), audio.read_bytes())
            self.assertEqual((root / 'voices' / 'foobar.txt').read_text(), transcript.read_text())
            self.assertEqual(result['prepared']['wav_sha256'], meta['wav_sha256'])
            with self.assertRaises(FileExistsError):
                reference.prepare(argparse.Namespace(
                    wav=audio, transcript=transcript, selected_transcript=None,
                    output_dir=root / 'voices', name='foobar', start=None, end=None,
                    normalize=False, force=False))
            with self.assertRaises(ValueError):
                reference.prepare(argparse.Namespace(
                    wav=audio, transcript=transcript, selected_transcript=None,
                    output_dir=root / 'voices', name='other', start=0, end=1,
                    normalize=False, force=False))

    def test_optional_reference_normalization(self):
        import shutil
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg is not installed')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / 'source.wav'
            raw.write_bytes(fake_wav(2.2))
            text = root / 'source.txt'
            text.write_text('A source reference contains two seconds of speech.')
            selected = root / 'selected.txt'
            selected.write_text('A source reference')
            result = reference.prepare(argparse.Namespace(
                wav=raw, transcript=text, selected_transcript=selected,
                output_dir=root / 'prepared', name='foobar', start=0.1, end=1.3,
                normalize=True, force=False))
            self.assertTrue(result['normalized_loudnorm'])
            self.assertEqual(result['selected_window_seconds'], [0.1, 1.3])
            self.assertEqual(result['prepared']['sample_rate'], 24000)
            self.assertAlmostEqual(result['prepared']['duration_seconds'], 1.2, delta=0.15)
            self.assertEqual((root/'prepared'/'foobar.txt').read_text(), selected.read_text())
            self.assertNotEqual((root/'prepared'/'foobar.wav').read_bytes(), raw.read_bytes())
            self.assertEqual(raw.read_bytes(), fake_wav(2.2))

    def test_output_dir_cannot_be_tracked_source(self):
        with self.assertRaises(ValueError):
            bench.verify_output_dir(bench.REPO / 'services' / 'tts' / 'benchmarks')
        with self.assertRaises(ValueError):
            reference.validate_output_dir(bench.REPO / 'services' / 'tts' / 'scripts')

    def test_lossless_join_and_pause_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root/'first.wav', root/'second.wav']
            for path in paths:
                path.write_bytes(fake_wav(1.0))
            result = bench.join_wavs(paths, root/'joined.wav', pause_seconds=0.25)
            self.assertEqual(result['joined_segments'], 2)
            self.assertEqual(result['expected_frames'], 18000)
            self.assertAlmostEqual(result['duration_seconds'], 2.25)
            self.assertEqual(result['frames'], 18000)

    def test_audio_metrics_reject_invalid_wav(self):
        self.assertAlmostEqual(bench.inspect_wav_bytes(fake_wav())['duration_seconds'], 1.4)
        with self.assertRaises((ValueError, wave.Error, EOFError)):
            bench.inspect_wav_bytes(b'not audio')

    def test_baseline_instruction_is_private_and_variant_specific(self):
        StubHandler.requests = []
        StubHandler.clipped = False
        server = ThreadingHTTPServer(('127.0.0.1', 0), StubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                instruction = root / 'delivery.txt'
                instruction.write_text('Read with the measured pace of a mathematics lecturer.')
                refwav = root / 'reference.wav'
                reftxt = root / 'reference.txt'
                refwav.write_bytes(fake_wav())
                reftxt.write_text('A reference transcript.')
                args = argparse.Namespace(url=f'http://127.0.0.1:{server.server_port}',
                                          voice='foobar', model=None, input=bench.DEFAULT_EXCERPT,
                                          output_dir=root / 'runs', variant=['short12', 'plain40'],
                                          seed=17, max_segments=1, timeout=10, join_pause=0.15,
                                          baseline_instruction_file=instruction,
                                          reference_wav=refwav, reference_transcript=reftxt)
                folder = bench.run(args)
                manifest = json.loads((folder/'manifest.json').read_text())
                self.assertEqual(manifest['reference_identity']['wav_sha256'], bench.sha256(refwav.read_bytes()))
                self.assertNotIn('A reference transcript.', json.dumps(manifest))
                self.assertNotIn('Read with the measured', json.dumps(manifest))
                self.assertEqual(StubHandler.requests[0]['instruction'], instruction.read_text())
                self.assertNotIn('instruction', StubHandler.requests[1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_benchmark_retains_requests_and_flags_review_not_completeness(self):
        StubHandler.requests = []
        StubHandler.clipped = False
        server = ThreadingHTTPServer(('127.0.0.1', 0), StubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                excerpt = root / 'speech.txt'
                excerpt.write_text('First we inspect the gap. The next example has two operators. '
                                   'Their eigenspaces rotate after a perturbation. A residual controls this rotation. '
                                   'The spectral theorem gives an orthogonal basis. '
                                   'A positive distance between spectral clusters can bound the angle of rotation. '
                                   'This distinction is important when comparing norms and interpreting perturbation errors.')
                args = argparse.Namespace(url=f'http://127.0.0.1:{server.server_port}',
                                          voice='foobar', model=None, input=excerpt,
                                          output_dir=root / 'runs', variant=['short12', 'directed40'],
                                          seed=42, max_segments=2, timeout=10, join_pause=0.15)
                folder = bench.run(args)
                manifest = json.loads((folder / 'manifest.json').read_text())
                self.assertEqual(manifest['errors'], 0)
                self.assertEqual(manifest['mechanically_valid'], 4)
                self.assertNotIn('model_dir', manifest['runtime'])
                self.assertNotIn('reference_wav', manifest['runtime'])
                self.assertEqual(len(StubHandler.requests), 4)
                self.assertTrue(all(req['voice'] == 'foobar' for req in StubHandler.requests))
                self.assertIn('instruction', StubHandler.requests[-1])
                self.assertNotIn('instruction', StubHandler.requests[0])
                results = json.loads((folder / 'results.json').read_text())
                self.assertTrue(all(r['status'] == 'needs_listening_review' for r in results))
                self.assertEqual(len(list(folder.glob('**/audio.wav'))), 4)
                self.assertEqual(len(list(folder.glob('*/joined.wav'))), 2)
                measurements = json.loads((folder/'short12'/'joined.metrics.json').read_text())
                self.assertEqual(measurements['joined_segments'], 2)
                self.assertGreater(measurements['duration_seconds'], 2.8)
                self.assertIn('completeness', (folder / 'LISTEN_REVIEW.tsv').read_text())
                # A valid-looking HTTP 200 with malformed bytes must not
                # publish an audio artifact as successful.
                StubHandler.clipped = True
                second = bench.run(args)
                failed = json.loads((second / 'manifest.json').read_text())
                self.assertEqual(failed['errors'], 4)
                self.assertEqual(len(list(second.glob('**/audio.wav'))), 0)
                self.assertEqual(len(list(second.glob('**/error.json'))), 4)
                self.assertEqual(len(list(second.glob('*/joined.wav'))), 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
