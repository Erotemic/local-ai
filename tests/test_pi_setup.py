"""Verify settings preservation, backups, and setup preflight gates."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

RECIPE = Path(__file__).resolve().parents[1] / 'harnesses/pi'
sys.path.insert(0, str(RECIPE))
import doctor
import guest_probe
import guest_setup
import setup as pi_setup
from preferences import desired_preferences, PACKAGE_VERSIONS


class PiSetupTests(unittest.TestCase):
    def fixture(self, root):
        path = root / 'settings.json'
        path.write_text(json.dumps({'litellm': {'providers': {'litellm-namek': {'apiKey': 'TEST-SECRET', 'baseUrl': 'http://10.77.0.1:14042'}}},
                                    'unrelated': {'preserve': True}, 'compaction': {'reserveTokens': 32768},
                                    'packages': [{'source': 'npm:pi-provider-litellm', 'extensions': ['./src/index.ts']}, 'npm:unrelated']}))
        for name, version in PACKAGE_VERSIONS.items():
            metadata = root / 'npm/node_modules' / name / 'package.json'
            metadata.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({'version': version}))
        payload = {'settings_path': str(path), 'settings_revision': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'pi_path': '/fake/pi', 'upgrade_pi': True, 'install_timeout': 1,
                   'preferences': desired_preferences(doctor.BASELINE, 'litellm-namek', 'model', 'medium')}
        return path, payload

    def test_merge_preserves_credentials_and_unmanaged_settings_with_private_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, payload = self.fixture(Path(tmp))
            before = path.read_bytes()
            with patch.object(guest_setup, 'execute', return_value='1.4.0\n'):
                result = guest_setup.apply(payload)
            self.assertEqual(result['status'], 'ok')
            updated = json.loads(path.read_text())
            self.assertEqual(updated['litellm']['providers']['litellm-namek']['apiKey'], 'TEST-SECRET')
            self.assertEqual(updated['compaction'], {'enabled': True, 'reserveTokens': 32768})
            self.assertEqual(updated['retry']['provider']['timeoutMs'], 900000)
            self.assertEqual(updated['defaultThinkingLevel'], 'medium')
            self.assertEqual(updated['pi-effort']['modelEffort']['litellm-namek/model'], 'medium')
            self.assertTrue(updated['unrelated']['preserve'])
            self.assertEqual(updated['packages'][0]['extensions'], ['./src/index.ts'])
            self.assertIn('npm:unrelated', updated['packages'])
            backup = Path(result['backup'])
            self.assertEqual(backup.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn('TEST-SECRET', json.dumps(result))
            payload['settings_revision'] = hashlib.sha256(path.read_bytes()).hexdigest()
            stable = path.read_bytes()
            with patch.object(guest_setup, 'execute', return_value='1.4.0\n') as command:
                self.assertEqual(guest_setup.apply(payload)['status'], 'ok')
            self.assertEqual(path.read_bytes(), stable)
            self.assertEqual(command.call_count, 1)  # Version check only; no reinstall.

    def test_settings_changed_after_preflight_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, payload = self.fixture(Path(tmp))
            path.write_text('{"concurrent": true}')
            with patch.object(guest_setup, 'execute') as command:
                result = guest_setup.apply(payload)
            self.assertEqual(result['status'], 'failed')
            command.assert_not_called()
            self.assertEqual(path.read_text(), '{"concurrent": true}')
            self.assertEqual(list(path.parent.glob('settings.json.before-*')), [])

    def test_package_failure_retains_backup_and_reports_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, payload = self.fixture(Path(tmp))
            (path.parent / 'npm/node_modules/pi-effort/package.json').unlink()
            before = path.read_bytes()
            with patch.object(guest_setup, 'execute', side_effect=['1.4.0\n', RuntimeError('package install failed')]):
                result = guest_setup.apply(payload)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(Path(result['backup']).read_bytes(), before)
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn('TEST-SECRET', json.dumps(result))

    def test_old_pi_can_be_blocked_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, payload = self.fixture(Path(tmp))
            payload['upgrade_pi'] = False
            before = path.read_bytes()
            with patch.object(guest_setup, 'execute', return_value='0.87.1\n'):
                result = guest_setup.apply(payload)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('settings.json.before-*')), [])

    def test_old_pi_is_upgraded_before_preferences_are_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, payload = self.fixture(Path(tmp))
            with patch.object(guest_setup, 'execute', side_effect=['0.87.1\n', '', '1.4.0\n']) as command:
                result = guest_setup.apply(payload)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(command.call_args_list[1].args[1], ['update'])
            self.assertEqual(json.loads(path.read_text())['retry']['maxRetries'], 5)

    def test_remote_program_runs_without_checkout_or_helper_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, payload = self.fixture(root)
            pi = root / 'pi'
            pi.write_text('#!' + sys.executable + '\nprint("1.4.0")\n')
            pi.chmod(0o700)
            payload['pi_path'] = str(pi)
            result = subprocess.run([sys.executable, '-B', '-c', pi_setup.remote_source()],
                                    input=json.dumps(payload), text=True, capture_output=True, cwd='/')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'ok')
            self.assertEqual(json.loads(path.read_text())['defaultThinkingLevel'], 'medium')
            self.assertNotIn('TEST-SECRET', result.stdout + result.stderr)

    def test_aivm_managed_node_works_without_login_shell_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, payload = self.fixture(root)
            arch = {'x86_64': 'x64', 'aarch64': 'arm64', 'armv7l': 'armv7l', 'ppc64le': 'ppc64le', 's390x': 's390x'}[guest_setup.platform.machine()]
            node_dir = root / '.local/share/aivm/node' / ('node-v22.19.0-linux-' + arch) / 'bin'
            node_dir.mkdir(parents=True)
            node = node_dir / 'node'
            node.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo v22.19.0; else exec ' + shlex.quote(sys.executable) + ' "$@"; fi\n')
            node.chmod(0o700)
            pi = root / 'pi'
            pi.write_text('#!/usr/bin/env node\nprint("1.4.0")\n')
            pi.chmod(0o700)
            payload['pi_path'] = str(pi)
            with patch.dict(os.environ, {'PATH': '/usr/bin:/bin', 'AIVM_NODE_ROOT': str(node_dir.parent.parent), 'AIVM_PI_NODE_BIN_DIR': ''}), patch.object(guest_setup.Path, 'home', return_value=root):
                environment = guest_setup.runtime_environment(str(pi))
                self.assertEqual(guest_setup.shutil.which('node', path=environment['PATH']), str(node))
                result = guest_setup.apply(payload)
            self.assertEqual(result['status'], 'ok', result)
            self.assertEqual(json.loads(path.read_text())['defaultThinkingLevel'], 'medium')

    def test_launcher_failure_reports_runtime_without_leaking_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, payload = self.fixture(Path(tmp))
            pi = Path(tmp) / 'pi'
            pi.write_text('#!/bin/sh\necho "TEST-SECRET ERR_MODULE_NOT_FOUND" >&2\nexit 42\n')
            pi.chmod(0o700)
            payload['pi_path'] = str(pi)
            before = path.read_bytes()
            result = guest_setup.apply(payload)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['command_diagnostics']['exit_code'], 42)
            self.assertEqual(result['command_diagnostics']['reason'], 'ERR_MODULE_NOT_FOUND')
            self.assertIn(str(pi), result['command_diagnostics']['command'])
            self.assertNotIn('TEST-SECRET', json.dumps(result))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('settings.json.before-*')), [])

    def test_host_does_not_apply_without_verified_connectivity(self):
        report = {'pi': {}, 'proposed_changes': [], 'warnings': []}
        output = io.StringIO()
        with patch.object(sys, 'argv', ['setup', '--model', 'model']), patch.object(doctor, 'collect', return_value=(report, None, None)), patch.object(doctor, 'run') as command, contextlib.redirect_stdout(output):
            self.assertEqual(pi_setup.main(), 1)
        command.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'], 'blocked')

    def test_missing_effort_metadata_does_not_block_local_settings(self):
        report = {
            'pi': {'pi_path': '/guest/pi', 'settings_path': '/guest/settings.json', 'settings_revision': 'revision',
                   'desired_endpoint': {'status': 'ok', 'models': ['model']},
                   'baseUrl': 'http://10.77.0.1:14042', 'credential_matches': True},
            'connection': {'preview_safe': True, 'provider_url': 'http://10.77.0.1:14042'},
            'selected_model': 'model', 'aivm': {},
            'desired_preferences': desired_preferences(doctor.BASELINE, 'litellm-namek', 'model', 'medium'),
            'model_capabilities': {'status': 'ok', 'reasoning_effort_levels': [], 'supports_reasoning': False},
            'warnings': ['Runtime effort support is unverified.'],
        }
        output = io.StringIO()
        with patch.object(sys, 'argv', ['setup', '--model', 'model', '--effort', 'medium', '--json']), patch.object(doctor, 'collect', return_value=(report, None, None)), patch.object(doctor, 'ssh_argv', return_value=['ssh', 'vm']), patch.object(doctor, 'run', return_value=(0, '{"status":"ok"}')) as command, contextlib.redirect_stdout(output):
            self.assertEqual(pi_setup.main(), 0)
        payload = json.loads(command.call_args.args[2])
        self.assertEqual(payload['preferences']['retry']['maxRetries'], 5)
        self.assertTrue(payload['preferences']['compaction']['enabled'])
        self.assertEqual(payload['preferences']['defaultThinkingLevel'], 'medium')
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(result['effort']['metadata_verified'])
        self.assertEqual(result['warnings'], report['warnings'])

    def test_capability_metadata_limits_effort_to_advertised_levels(self):
        reply = {'status': 'ok', 'data': {'data': [{'model_name': 'model', 'model_info': {'supports_reasoning': True, 'reasoning_effort_levels': ['low', 'medium', 'xhigh']}}]}}
        with patch.object(guest_probe, 'gateway_json', return_value=reply):
            result = guest_probe.capabilities('http://127.0.0.1:14042', 'TEST-SECRET', 1, 'model')
        self.assertEqual(result['reasoning_effort_levels'], ['low', 'medium', 'xhigh'])
        self.assertNotIn('TEST-SECRET', json.dumps(result))

    def test_unknown_explicit_provider_does_not_borrow_another_providers_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.fixture(Path(tmp))
            with patch.dict(guest_probe.os.environ, {'PI_CODING_AGENT_DIR': str(path.parent), 'LITELLM_API_KEY': ''}):
                result = guest_probe.inspect_guest({'provider': 'typo', 'url': None, 'key': 'TEST-SECRET', 'timeout': 1})
            self.assertEqual(result['provider'], 'typo')
            self.assertIsNone(result['credential_matches'])


if __name__ == '__main__':
    unittest.main()
