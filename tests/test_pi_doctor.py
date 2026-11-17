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
from types import SimpleNamespace

RECIPE = Path(__file__).resolve().parents[1] / 'harnesses/pi'
sys.path.insert(0, str(RECIPE))
import doctor
import guest_probe


class PiDoctorTests(unittest.TestCase):
    def test_live_network_reads_active_xml(self):
        vm = {'network': {'name': 'aivm-net'}}
        report = {'notes': [], 'warnings': []}
        xml = '<network><bridge name="virbr-aivm"/><ip address="10.77.0.1" netmask="255.255.255.0"/></network>'
        with patch.object(doctor, 'run', side_effect=[(0, 'Active: yes\n'), (0, xml)]) as command:
            live = doctor.query_live_network(SimpleNamespace(timeout=1), vm, report)
        self.assertEqual(live['gateway_ip'], '10.77.0.1')
        self.assertEqual(live['subnet_cidr'], '10.77.0.0/24')
        self.assertTrue(all(call.args[0][:2] == ['virsh', '--readonly'] for call in command.call_args_list))

    def test_namek_drift_withholds_incorrect_endpoint_exception(self):
        vm = {'network': {'name': 'aivm-net', 'gateway_ip': '10.78.0.1', 'subnet_cidr': '10.78.0.0/24', 'bridge': 'virbr-aivm'},
              'cached_ip': '10.77.0.126', 'firewall': {'enabled': True, 'allow_tcp_endpoints': ['10.77.0.1:14042']}}
        report = {'notes': [], 'warnings': [], 'proposed_changes': []}
        doctor.check_network_drift(vm, None, report)
        with patch.object(doctor, 'run', return_value=(1, '')):
            doctor.inspect_network(SimpleNamespace(timeout=1), vm, 'http://10.78.0.1:14042', report)
        self.assertFalse(report['connection']['preview_safe'])
        self.assertEqual(report['proposed_changes'], [])
        self.assertIn('outside configured subnet', report['network_drift'][0])

    def test_guest_route_disagreement_withholds_endpoint_changes(self):
        guest = {'pi_path': '/bin/pi', 'packages': ['npm:pi-provider-litellm', 'npm:pi-effort'],
                 'theme': 'dark', 'provider': 'litellm', 'defaultProvider': 'litellm',
                 'baseUrl': 'http://10.77.0.1:14042', 'allowInsecureHttp': True,
                 'credential_matches': True, 'defaultModel': 'model',
                 'desired_endpoint': {'status': 'URLError'},
                 'current_endpoint': {'status': 'ok', 'models': ['model']},
                 'package_versions': doctor.PACKAGE_VERSIONS,
                 'preferences': doctor.desired_preferences(doctor.BASELINE, 'litellm', 'model'),
                 'default_routes': [{'gateway': '10.77.0.1', 'metric': 100}]}
        report = {'notes': [], 'warnings': [], 'proposed_changes': ['Add 10.78.0.1:14042 to network aivm-net firewall.allow_tcp_endpoints (preserve other entries).'],
                  'connection_gateway': '10.78.0.1', 'connection': {'preview_safe': True, 'endpoint': '10.78.0.1:14042'}}
        args = SimpleNamespace(skip_guest=False, timeout=1, provider=None, model='model')
        with patch.object(doctor, 'ssh_argv', return_value=['ssh', 'vm']), patch.object(doctor, 'run', return_value=(0, json.dumps(guest))):
            doctor.inspect_pi(args, {}, 'http://10.78.0.1:14042', 'TEST', report)
        self.assertFalse(report['connection']['preview_safe'])
        self.assertEqual(report['proposed_changes'], [])

    def test_linux_default_route_decoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            route = Path(tmp) / 'route'
            route.write_text('Iface Destination Gateway Flags RefCnt Use Metric Mask\nens3 00000000 01004D0A 0003 0 0 100 00000000\n')
            self.assertEqual(guest_probe.default_routes(route), [{'interface': 'ens3', 'gateway': '10.77.0.1', 'metric': 100}])

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
        vm = {'network': {'name': 'aivm-net'}, 'firewall': {'enabled': False, 'allow_tcp_endpoints': ['10.77.0.1:14042'], 'allow_tcp_ports': ['14042']}}
        report = {'warnings': [], 'notes': [], 'proposed_changes': []}
        with patch.object(doctor, 'run', return_value=(1, '')):
            doctor.inspect_network(type('Args', (), {'timeout': 1})(), vm, 'http://10.77.0.1:14042', report)
        self.assertEqual(report['proposed_changes'], [])
        self.assertFalse(report['connection']['firewall_enabled'])
        self.assertTrue(any('broad TCP port exception' in note for note in report['notes']))

    def test_missing_tools_remain_shareable_and_nonfatal(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['doctor', '--json']), patch.object(doctor.shutil, 'which', return_value=None), contextlib.redirect_stdout(output):
            self.assertEqual(doctor.main(), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report['read_only'])
        self.assertEqual(len(report['warnings']), 2)


if __name__ == '__main__':
    unittest.main()
