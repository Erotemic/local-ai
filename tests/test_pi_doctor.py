"""Read-only and secret-handling checks for the host pi doctor."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

RECIPE = Path(__file__).resolve().parents[1] / 'harnesses/pi'
sys.path.insert(0, str(RECIPE))
import doctor
import guest_probe


class PiDoctorTests(unittest.TestCase):
    def test_authenticated_models_get_does_not_follow_redirects(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get('Authorization')))
                self.send_response(302)
                self.send_header('Location', '/must-not-follow')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = guest_probe.models('http://127.0.0.1:' + str(server.server_port) + '/v1', 'TEST-SECRET', 1)
            self.assertEqual(result['status'], 'HTTP 302')
            self.assertEqual(requests, [('/v1/models', 'Bearer TEST-SECRET')])
            self.assertNotIn('TEST-SECRET', json.dumps(result))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_endpoint_translation_preserves_remote_and_custom_prefix(self):
        self.assertEqual(doctor.desired_url('http://127.0.0.1:14042/v1', '10.78.0.1'), 'http://10.78.0.1:14042')
        self.assertEqual(doctor.desired_url('http://0.0.0.0:14042/v1', '10.77.0.1'), 'http://10.77.0.1:14042')
        self.assertEqual(doctor.desired_url('https://models.example/proxy/v1', '10.78.0.1'), 'https://models.example/proxy')

    def test_url_redacts_credentials_and_query(self):
        value = guest_probe.safe_url('https://user:SECRET@models.example:8443/v1?key=SECRET')
        self.assertEqual(value, 'https://models.example:8443/v1')

    def test_guard_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'must-not-exist'
            code = 'import sys; from aivm_probe import deny_writes; sys.addaudithook(deny_writes); open(sys.argv[1], "w")'
            proc = subprocess.run([sys.executable, '-B', '-c', code, str(path)], cwd=RECIPE, capture_output=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(path.exists())

    def test_stack_guard_rejects_swallowed_write_and_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / 'infer-stack'
            target = Path(tmp) / 'must-not-exist'
            cli.write_text('try:\n open(' + repr(str(target)) + ', "w")\nexcept OSError:\n pass\nprint("incorrect-fallback-secret")\n')
            proc = subprocess.run([sys.executable, '-B', str(RECIPE / 'stack_probe.py')], input=json.dumps([str(cli), 'LITELLM_MASTER_KEY']), text=True, capture_output=True)
            self.assertEqual(proc.returncode, 76)
            self.assertEqual(proc.stdout, '')
            self.assertFalse(target.exists())

    def test_guest_comparison_omits_keys_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / 'settings.json'
            settings.write_text(json.dumps({'defaultProvider': 'litellm', 'litellm': {'providers': {'litellm': {'baseUrl': 'http://127.0.0.1:14042', 'apiKey': 'TEST-SECRET'}}}}))
            before = settings.read_bytes()
            with patch.dict(os.environ, {'PI_CODING_AGENT_DIR': tmp}), patch.object(guest_probe, 'models', return_value={'status': 'ok', 'models': ['model']}):
                result = guest_probe.inspect_guest({'url': 'http://10.77.0.1:14042', 'key': 'TEST-SECRET', 'timeout': 1})
            self.assertTrue(result['credential_matches'])
            self.assertNotIn('TEST-SECRET', json.dumps(result))
            self.assertEqual(settings.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ['settings.json'])

    def test_credential_commands_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'must-not-exist'
            (Path(tmp) / 'settings.json').write_text(json.dumps({'litellm': {'providers': {'litellm': {'apiKey': '!touch ' + str(target)}}}}))
            with patch.dict(os.environ, {'PI_CODING_AGENT_DIR': tmp}):
                result = guest_probe.inspect_guest({'url': None, 'key': 'TEST', 'timeout': 1})
            self.assertIsNone(result['credential_matches'])
            self.assertFalse(target.exists())

    def test_firewall_change_deduplicated_disabled_policy_preserved(self):
        vm = {'network': {'name': 'aivm-net'}, 'firewall': {'enabled': False, 'allow_tcp_endpoints': ['10.77.0.1:14042']}}
        report = {'warnings': [], 'notes': [], 'proposed_changes': []}
        with patch.object(doctor, 'run', return_value=(1, '')):
            doctor.inspect_network(type('Args', (), {'timeout': 1})(), vm, 'http://10.77.0.1:14042', report)
        self.assertEqual(report['proposed_changes'], [])
        self.assertFalse(report['connection']['firewall_enabled'])

    def test_missing_tools_remain_shareable_and_nonfatal(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['doctor', '--json']), patch.object(doctor.shutil, 'which', return_value=None), contextlib.redirect_stdout(output):
            self.assertEqual(doctor.main(), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report['read_only'])
        self.assertEqual(len(report['warnings']), 2)


if __name__ == '__main__':
    unittest.main()
