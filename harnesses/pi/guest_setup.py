"""Apply personal settings to an already provisioned pi installation."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from preferences import merge, MIN_PI_VERSION, PACKAGE_VERSIONS


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix='.local-ai-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def execute(pi, arguments, timeout, environment):
    try:
        result = subprocess.run([pi] + arguments, cwd='/', env=environment,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Pi command failed or timed out: ' + ' '.join(arguments)) from None
    if result.returncode:
        raise RuntimeError('Pi command failed: ' + ' '.join(arguments) + '; run that command in the guest to inspect its output.')
    return result.stdout


def _apply(payload):
    path = Path(payload['settings_path'])
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Expected an existing regular pi settings file; no changes made.')
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != payload['settings_revision']:
        raise RuntimeError('Pi settings changed since preflight; rerun setup with pi closed.')
    initial = json.loads(original)
    if not isinstance(initial, dict):
        raise RuntimeError('Pi settings must be a JSON object.')
    pi = payload['pi_path']
    environment = dict(os.environ)
    extra_paths = [str(Path(pi).parent), str(Path.home() / '.local/bin'), str(Path.home() / '.local/share/pi-node/current/bin')]
    environment['PATH'] = os.pathsep.join(extra_paths + [environment.get('PATH', '/usr/bin:/bin')])
    version_output = execute(pi, ['--version'], 30, environment)
    match = re.search(r'(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)', version_output)
    if not match:
        raise RuntimeError('Could not identify the installed pi version; no changes made.')
    version = tuple(map(int, match.groups()))
    if version < MIN_PI_VERSION and not payload['upgrade_pi']:
        raise RuntimeError('This recipe requires pi >=1.4.0; allow setup to upgrade pi.')
    fd, backup_name = tempfile.mkstemp(prefix='settings.json.before-local-ai-', dir=path.parent)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(original)
    result = {'backup': backup_name, 'settings_path': str(path), 'packages': PACKAGE_VERSIONS}
    try:
        if version < MIN_PI_VERSION:
            execute(pi, ['update'], payload['install_timeout'], environment)
            current = execute(pi, ['--version'], 30, environment)
            match = re.search(r'(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)', current)
            if not match or tuple(map(int, match.groups())) < MIN_PI_VERSION:
                raise RuntimeError('Pi update did not reach the recipe baseline >=1.4.0.')
        root = path.parent
        for package, wanted in PACKAGE_VERSIONS.items():
            metadata = root / 'npm/node_modules' / package / 'package.json'
            try:
                installed = json.loads(metadata.read_text()).get('version')
            except (OSError, ValueError):
                installed = None
            if installed != wanted:
                execute(pi, ['install', 'npm:' + package + '@' + wanted], payload['install_timeout'], environment)
                try:
                    actual = json.loads(metadata.read_text()).get('version')
                except (OSError, ValueError):
                    actual = None
                if actual != wanted:
                    raise RuntimeError('Could not verify installed package ' + package + ' at the requested version.')
        before_merge = path.read_bytes()
        settings = json.loads(before_merge)
        updated = merge(settings, payload['preferences'])
        # Replace only the two recipe package declarations; retain resource
        # filters on package objects and every unrelated package/provider.
        packages, seen = [], set()
        for entry in updated.get('packages', []):
            source = entry.get('source') if isinstance(entry, dict) else entry
            name = next((name for name in PACKAGE_VERSIONS if isinstance(source, str) and
                         (source == 'npm:' + name or source.startswith('npm:' + name + '@'))), None)
            if name:
                if name in seen:
                    continue
                seen.add(name)
                pinned = 'npm:' + name + '@' + PACKAGE_VERSIONS[name]
                packages.append(dict(entry, source=pinned) if isinstance(entry, dict) else pinned)
            else:
                packages.append(entry)
        packages.extend('npm:' + name + '@' + version for name, version in PACKAGE_VERSIONS.items() if name not in seen)
        updated['packages'] = packages
        if path.read_bytes() != before_merge:
            raise RuntimeError('Pi settings changed during setup; refusing to overwrite concurrent edits.')
        if updated != settings:
            atomic_write(path, (json.dumps(updated, indent=2) + '\n').encode())
        result['status'] = 'ok'
    except Exception as exc:
        # Package installation may already have changed settings/cache. Keep
        # the private backup; do not clobber concurrent edits with a rollback.
        result['status'] = 'failed'
        result['error'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    return result


def apply(payload):
    try:
        return _apply(payload)
    except Exception as exc:
        return {'status': 'failed', 'error': str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}
