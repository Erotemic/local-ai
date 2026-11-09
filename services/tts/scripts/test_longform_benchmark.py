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
        if req.get('cfg_scale', 1.0) > 1 and not req.get('instruction'):
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":"clone template does not define a negative prompt"}')
            return
        self.reply(b'not a WAV' if self.clipped else fake_wav(), 'audio/wav')


class LongformTests(unittest.TestCase):
    def test_public_voice_default_and_environment_override(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=True):
            args = bench.make_parser().parse_args(['run', '--url', 'http://localhost:8821',
                                                    '--output-dir', '/tmp/breeze-out'])
            self.assertEqual(args.voice, 'crall-math-dk-v2')
        with patch.dict(os.environ, {'TTS_BENCH_VOICE': 'foobar'}):
            args = bench.make_parser().parse_args(['run', '--url', 'http://localhost:8821',
                                                    '--output-dir', '/tmp/breeze-out'])
            self.assertEqual(args.voice, 'foobar')
        args = bench.make_parser().parse_args(['run', '--url', 'http://localhost:8821',
                                               '--output-dir', '/tmp/breeze-out', '--voice', 'ryan'])
        self.assertEqual(args.voice, 'ryan')

    def test_sentence_splitting_and_passage_coverage(self):
        text = 'Dr. Brown studies matrices. The eigenspaces are stable. \'A spectral gap helps.\' Next we compare them!'
        self.assertEqual(' '.join(bench.sentences(text)), text)
        for size in [12, 25, 40]:
            chunks = bench.passages(text, size)
            self.assertEqual(' '.join(chunks), text)
            self.assertTrue(all(len(c.split()) <= size for c in chunks))
        self.assertRaises(ValueError, bench.passages, 'short', 1)
        self.assertRaises(ValueError, bench.sentences, '')
        self.assertEqual(' '.join(bench.passages('One complete paragraph.\n\nAnother complete paragraph.', 40)),
                         'One complete paragraph. Another complete paragraph.')

    def test_full_excerpt_is_identical_across_trial_policies(self):
        source = bench.DEFAULT_EXCERPT.read_text(encoding='utf8')
        excerpt, planned = bench.shared_excerpt(
            source, {k:bench.VARIANTS[k] for k in bench.DEFAULT_VARIANTS}, None)
        self.assertEqual(bench.normalize(excerpt), bench.normalize(source))
        self.assertEqual(len(excerpt.split()), 148)
        self.assertEqual(len(planned['plain40']), len(planned['directed40']))
        self.assertGreater(len(planned['plain40']), len(planned['paragraph80']))
        self.assertEqual([p.text for p in planned['plain40']],
                         [p.text for p in planned['directed40']])
        for key, pieces in planned.items():
            with self.subTest(key=key):
                self.assertEqual(bench.normalize(' '.join(p.text for p in pieces)),
                                 bench.normalize(excerpt))
                self.assertEqual(pieces[0].word_start, 0)
                self.assertEqual(pieces[-1].word_end, 148)
                self.assertTrue(all(p.words <= bench.VARIANTS[key]['max_words'] for p in pieces))
                self.assertFalse(any(p.forced_sentence_split for p in pieces))
                self.assertTrue(all(p.word_end == q.word_start
                                    for p,q in zip(pieces,pieces[1:])))

    def test_common_segment_budget_selects_same_complete_sentence_prefix(self):
        source = bench.DEFAULT_EXCERPT.read_text(encoding='utf8')
        chosen = {key:bench.VARIANTS[key]
                  for key in ('short12','plain40','directed40','paragraph80')}
        selected, plans = bench.shared_excerpt(source, chosen, 3)
        self.assertLess(len(selected.split()), len(source.split()))
        self.assertTrue(all(len(p) <= 3 for p in plans.values()))
        self.assertTrue(all(bench.normalize(' '.join(s.text for s in p)) ==
                            bench.normalize(selected) for p in plans.values()))
        # A complete-sentence prefix is equivalent to an initial sequence
        # of original sentences, never a floating word-budget cutoff.
        source_sentences = [sentence for paragraph in source.split('\n\n')
                            for sentence in bench.sentences(paragraph)]
        self.assertTrue(any(bench.normalize(selected) == bench.normalize(' '.join(source_sentences[:n]))
                            for n in range(1,len(source_sentences)+1)))
        self.assertEqual([p.text for p in plans['plain40']],
                         [p.text for p in plans['directed40']])
        with self.assertRaisesRegex(ValueError, 'first complete sentence'):
            bench.shared_excerpt(' '.join(['statement']*110)+'.',
                                 {'tiny':dict(target_words=3,max_words=5)}, 1)

    def test_sentence_preserving_balancing_and_rare_forced_splits(self):
        from passage_planner import plan_passages, sentences
        paragraph = ('Dr. Chen computes a stable invariant subspace. '
                     'The first trial demonstrates the spectral gap. '
                     'The next trial gives a corresponding perturbation theorem. '
                     'The conclusion follows for each tested operator norm.')
        groups = plan_passages(paragraph, target_words=16, hard_max_words=24)
        self.assertTrue(all(p.words <= 24 for p in groups))
        self.assertTrue(all(p.boundary_before in ('start','sentence') for p in groups))
        self.assertEqual(bench.normalize(' '.join(p.text for p in groups)), bench.normalize(paragraph))
        self.assertFalse(any(p.forced_sentence_split for p in groups))
        self.assertEqual(bench.sentences('Dr. Chen works. She studies Fig. 3. The result is true.'),
                         ['Dr. Chen works.', 'She studies Fig. 3.', 'The result is true.'])
        self.assertEqual(sentences('"The result follows." Next, review it.'),
                         ['"The result follows."', 'Next, review it.'])
        # 49 words with max 24 should become balanced ~16-word pieces, not
        # 24 + 24 + one isolated word.
        lengthy = ' '.join(['projection']*48 + ['closes.'])
        oversized = plan_passages(lengthy, target_words=20, hard_max_words=24)
        self.assertEqual(sum(p.words for p in oversized),49)
        self.assertEqual(len(oversized),3)
        self.assertTrue(all(15 <= p.words <= 24 for p in oversized))
        self.assertTrue(any(p.boundary_before=='forced_sentence_split' for p in oversized[1:]))
        self.assertEqual(bench.normalize(' '.join(p.text for p in oversized)), lengthy)

    def test_per_boundary_pause_preserves_pcm_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = [root / f'{n}.wav' for n in range(3)]
            for path in paths: path.write_bytes(fake_wav(.75))
            result = bench.join_wavs(paths, root/'joined.wav', pause_seconds=[.10,.30])
            self.assertEqual(result['expected_frames'], 3*6000 + 800 + 2400)
            self.assertEqual(result['join_pauses_seconds'], [.1,.3])
            with self.assertRaisesRegex(ValueError, 'one finite nonnegative pause'):
                bench.join_wavs(paths, root/'bad.wav', pause_seconds=[.1])

    def test_variant_cfg_matches_breeze_template_requirements(self):
        self.assertEqual(bench.VARIANTS['short12']['cfg_scale'], 1.0)
        self.assertEqual(bench.VARIANTS['plain40']['cfg_scale'], 1.0)
        self.assertEqual(bench.VARIANTS['paragraph80']['cfg_scale'], 1.0)
        self.assertEqual(bench.VARIANTS['directed40']['cfg_scale'], 2.0)
        self.assertIsNone(bench.variant_instruction('short12'))
        self.assertIsNotNone(bench.variant_instruction('directed40'))
        for name, spec in bench.VARIANTS.items():
            bench.validate_breeze_cfg(spec['cfg_scale'], bench.variant_instruction(name))
        with self.assertRaisesRegex(ValueError, 'requires a nonempty instruction'):
            bench.validate_breeze_cfg(2.0, None)
        with self.assertRaisesRegex(ValueError, 'requires a nonempty instruction'):
            bench.validate_breeze_cfg(2.0, '')
        with self.assertRaisesRegex(ValueError, 'positive and finite'):
            bench.validate_breeze_cfg(float('nan'), 'Some instruction')
        bench.validate_breeze_cfg(2.0, 'A gentle direction')
        self.assertEqual(bench.variant_instruction('short12', 'Private instruction'), 'Private instruction')
        self.assertEqual(bench.variant_instruction('plain40', 'Private instruction'), None)

    def test_negative_prompt_error_has_actionable_redacted_category(self):
        error = bench.RequestFailure(400,
                '{"error": "Template ref_clone_tata does not define a negative prompt but cfg_scale=2.0"}')
        label = bench.failure_label(error)
        self.assertIn('CFG > 1', label)
        self.assertNotIn('ref_clone_tata', label)
        self.assertEqual(bench._retained_error_category({'status': 'error', 'error': str(error)}), label)

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
                self.assertEqual(manifest['mechanically_valid'], 3)
                self.assertNotIn('model_dir', manifest['runtime'])
                self.assertNotIn('reference_wav', manifest['runtime'])
                self.assertEqual(len(StubHandler.requests), 3)
                self.assertTrue(all(req['voice'] == 'foobar' for req in StubHandler.requests))
                self.assertEqual(StubHandler.requests[0]['cfg_scale'], 1.0)
                self.assertNotIn('instruction', StubHandler.requests[0])
                self.assertIn('instruction', StubHandler.requests[-1])
                self.assertNotIn('instruction', StubHandler.requests[0])
                results = json.loads((folder / 'results.json').read_text())
                self.assertTrue(all(r['status'] == 'needs_listening_review' for r in results))
                self.assertEqual(len(list(folder.glob('**/audio.wav'))), 3)
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
                self.assertEqual(failed['errors'], 3)
                self.assertEqual(len(list(second.glob('**/audio.wav'))), 0)
                self.assertEqual(len(list(second.glob('**/error.json'))), 3)
                self.assertEqual(len(list(second.glob('*/joined.wav'))), 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()


class RejectHandler(StubHandler):
    requests = []

    def do_GET(self):
        if self.path == '/v1/models':
            body = {'data': [{'id': 'breeze-tts2', 'speakers': ['foobar', 'ryan']}]}
            self.reply(json.dumps(body).encode(), 'application/json')
        else:
            super().do_GET()

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(request)
        response = json.dumps({'error': "voice 'foobar' has no matching transcript"}).encode()
        self.send_response(400)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class FailureDiagnosticsTests(unittest.TestCase):
    def test_empty_voice_is_rejected_before_network_or_artifacts(self):
        for bad in ('', '  ', '\t'):
            with self.subTest(bad=repr(bad)):
                with self.assertRaisesRegex(ValueError, 'voice'):
                    bench.validate_voice(bad, {})
        with self.assertRaisesRegex(ValueError, 'not listed'):
            bench.validate_voice('unknown', {'speakers': ['foobar']})
        bench.validate_voice('foobar', {'speakers': ['foobar']})

    def test_http_configuration_error_stops_run_and_redacts_report(self):
        import contextlib
        RejectHandler.requests = []
        server = ThreadingHTTPServer(('127.0.0.1', 0), RejectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                args = argparse.Namespace(
                    url=f'http://127.0.0.1:{server.server_port}', voice='foobar',
                    model=None, input=bench.DEFAULT_EXCERPT, output_dir=root / 'runs',
                    variant=None, seed=42, max_segments=3, timeout=10, join_pause=0.15)
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    path = bench.run(args)
                self.assertEqual(len(RejectHandler.requests), 1)
                summary = bench.report(path)
                self.assertEqual(summary['failures'], 1)
                self.assertIn('HTTP 400', captured.getvalue())
                self.assertNotIn('foobar', captured.getvalue())
                self.assertNotIn('foobar', json.dumps(summary))
                manifest = json.loads((path / 'manifest.json').read_text())
                self.assertEqual(manifest['status'], 'aborted')
                self.assertEqual(manifest['segments'], 1)
                # Old retained runs should be reportable without leaking details.
                rows = json.loads((path / 'results.json').read_text())
                rows[0].pop('error_category')
                bench.atomic_json(path / 'results.json', rows)
                summary = bench.report(path)
                self.assertEqual(summary['failures'], 1)
                self.assertNotIn('foobar', json.dumps(summary))
                self.assertIn('HTTP 400', next(iter(summary['categories'])))
                self.assertEqual(len(list(path.glob('**/audio.wav'))), 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_cli_reports_failure_exit_status_and_report(self):
        import contextlib
        RejectHandler.requests = []
        server = ThreadingHTTPServer(('127.0.0.1', 0), RejectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                args = ['run', '--url', f'http://127.0.0.1:{server.server_port}',
                        '--voice', 'foobar', '--output-dir', str(root / 'runs'),
                        '--max-segments', '3']
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = bench.main(args)
                self.assertEqual(status, 1)
                self.assertIn('synthesis failures', stderr.getvalue())
                run_dir = next((root / 'runs').iterdir())
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(bench.main(['report', '--run-dir', str(run_dir)]), 0)
                self.assertEqual(json.loads(stdout.getvalue())['failures'], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class EqualTextPipelineTests(unittest.TestCase):
    def test_full_same_excerpt_run_and_focused_join_previews(self):
        StubHandler.requests = []
        StubHandler.clipped = False
        server = ThreadingHTTPServer(('127.0.0.1',0), StubHandler)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                args = argparse.Namespace(url=f'http://127.0.0.1:{server.server_port}',
                                          voice='foobar', model=None, input=bench.DEFAULT_EXCERPT,
                                          output_dir=root/'runs', variant=None, seed=42,
                                          max_segments=None, timeout=15, join_pause=.15,
                                          paragraph_pause=.30)
                out = bench.run(args)
                manifest = json.loads((out/'manifest.json').read_text())
                self.assertEqual(manifest['comparison']['source_words'],148)
                self.assertEqual(manifest['comparison']['evaluated_words'],148)
                self.assertFalse(manifest['comparison']['truncated_for_budget'])
                self.assertEqual(manifest['comparison']['segments_per_variant'],
                                 {'plain40':4, 'directed40':4, 'paragraph80':3})
                self.assertEqual(len(StubHandler.requests),11)
                self.assertTrue((out/'evaluated.txt').is_file())
                self.assertEqual(bench.normalize((out/'evaluated.txt').read_text()),
                                 bench.normalize(bench.DEFAULT_EXCERPT.read_text()))
                self.assertEqual(manifest['mechanically_valid'],11)
                self.assertEqual(manifest['errors'],0)
                hashes = set()
                for name in bench.DEFAULT_VARIANTS:
                    plan = json.loads((out/name/'plan.json').read_text())
                    hashes.add(plan['shared_excerpt_sha256'])
                    segment_texts = [json.loads(p.read_text())['input']
                                     for p in sorted((out/name).glob('[0-9][0-9][0-9]/request.json'))]
                    self.assertEqual(bench.normalize(' '.join(segment_texts)),
                                     bench.normalize((out/'evaluated.txt').read_text()))
                    self.assertEqual(len(list((out/name).glob('join-*.wav'))),len(segment_texts)-1)
                    self.assertEqual(len(list((out/name).glob('join-*.json'))),len(segment_texts)-1)
                    joined = json.loads((out/name/'joined.metrics.json').read_text())
                    self.assertEqual(joined['shared_excerpt_sha256'],plan['shared_excerpt_sha256'])
                    self.assertEqual(joined['joined_segments'],len(segment_texts))
                self.assertEqual(len(hashes),1)
                joins=(out/'JOIN_REVIEW.tsv').read_text().splitlines()
                self.assertEqual(len(joins)-1,8)
                self.assertTrue(all('join-' in line for line in joins[1:]))
                summary=bench.report(out)
                self.assertEqual(summary['comparison']['evaluated_words'],148)
                self.assertEqual(summary['variants']['plain40']['words'],148)
                self.assertEqual(summary['variants']['paragraph80']['words'],148)
                self.assertNotIn('foobar',json.dumps(summary))
                self.assertIn('cadence_inside_sentence',(out/'LISTEN_REVIEW.tsv').read_text())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_bounded_run_equal_excerpts_and_no_mid_sentence_budget_cut(self):
        StubHandler.requests = []
        StubHandler.clipped = False
        server = ThreadingHTTPServer(('127.0.0.1',0), StubHandler)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                args=argparse.Namespace(url=f'http://127.0.0.1:{server.server_port}',
                                        voice='foobar',model=None,input=bench.DEFAULT_EXCERPT,
                                        output_dir=root/'runs',variant=['short12','plain40','paragraph80'],
                                        seed=17,max_segments=3,timeout=15,join_pause=.15,
                                        paragraph_pause=.30)
                out=bench.run(args)
                manifest=json.loads((out/'manifest.json').read_text())
                self.assertTrue(manifest['comparison']['truncated_for_budget'])
                self.assertLess(manifest['comparison']['evaluated_words'],148)
                excerpt=bench.normalize((out/'evaluated.txt').read_text())
                for name in args.variant:
                    rows=json.loads((out/name/'plan.json').read_text())['segments']
                    self.assertLessEqual(len(rows),3)
                    self.assertEqual(bench.normalize(' '.join(r['text'] for r in rows)),excerpt)
                self.assertEqual(bench.report(out)['failures'],0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_join_window_preview_uses_end_and_start_without_reencoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=root/'a.wav';b=root/'b.wav';out=root/'join.wav'
            a.write_bytes(fake_wav(4.0));b.write_bytes(fake_wav(3.0))
            info=bench.boundary_preview(a,b,out,pause_seconds=.30,context_seconds=1.5)
            self.assertEqual(info['expected_frames'],round((1.5+.3+1.5)*8000))
            self.assertEqual(bench.inspect_wav(out)['frames'],info['expected_frames'])
            with self.assertRaisesRegex(ValueError,'matching PCM16'):
                mismatch=root/'mismatch.wav'
                with wave.open(str(mismatch),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
                    wav.writeframes(b'\0\0'*16000)
                bench.boundary_preview(a,mismatch,out,pause_seconds=.1)
