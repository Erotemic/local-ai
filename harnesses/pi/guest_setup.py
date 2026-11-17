"""Apply personal settings to an already provisioned pi installation."""
import hashlib
import errno
import json
import os
from pathlib import Path
import re
import platform
import shlex
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from preferences import merge, MIN_PI_VERSION, PACKAGE_VERSIONS


def runtime_environment(pi):
    environment = dict(os.environ)
    home = Path.home()
    node_root = Path(environment.get('AIVM_NODE_ROOT') or home / '.local/share/aivm/node').expanduser()
    arch = {'x86_64': 'x64', 'aarch64': 'arm64', 'armv7l': 'armv7l', 'ppc64le': 'ppc64le', 's390x': 's390x'}.get(platform.machine())
    candidates = []
    for directory in node_root.glob('node-v*-linux-*/bin'):
        match = re.fullmatch(r'node-v(\d+)\.(\d+)\.(\d+)-linux-(.+)', directory.parent.name)
        if not match or (arch and match[4] != arch):
            continue
        version = tuple(map(int, match.groups()[:3]))
        if version >= (22, 19, 0) and os.access(directory / 'node', os.X_OK):
            candidates.append((version, directory))
    managed = [str(directory) for _, directory in sorted(candidates, reverse=True)]
    explicit = environment.get('AIVM_PI_NODE_BIN_DIR')
    if explicit and os.access(Path(explicit).expanduser() / 'node', os.X_OK):
        managed.insert(0, str(Path(explicit).expanduser()))
    extra = [str(Path(pi).parent)] + managed + [str(home / '.local/share/pi-node/current/bin'), str(home / '.local/bin')]
    environment['PATH'] = os.pathsep.join(extra + [environment.get('PATH', '/usr/bin:/bin')])
    return environment


class PiCommandError(RuntimeError):
    def __init__(self, pi, arguments, environment, reason, returncode=None):
        command = shlex.join([pi] + arguments)
        super().__init__('Pi command failed: ' + command + ' (' + reason + ').')
        node = shutil.which('node', path=environment['PATH'])
        node_version = None
        if node:
            try:
                probe = subprocess.run([node, '--version'], env=environment, cwd='/', stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True, timeout=10)
                match = re.fullmatch(r'v?(\d+\.\d+\.\d+)\s*', probe.stdout)
                if probe.returncode == 0 and match:
                    node_version = match[1]
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.diagnostics = {'command': command, 'cwd': '/', 'exit_code': returncode,
                            'reason': reason, 'pi_resolved': str(Path(pi).resolve()),
                            'node': node, 'node_version': node_version,
                            'npm': shutil.which('npm', path=environment['PATH']),
                            'hint': 'Run the reported command inside the guest if this persists. Raw command output is omitted to protect credentials.'}


def failure(exc):
    result = {'status': 'failed', 'error': str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}
    if isinstance(exc, PiCommandError):
        result['command_diagnostics'] = exc.diagnostics
    return result


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


def backup(path, content):
    fd, name = tempfile.mkstemp(prefix=path.name + '.before-local-ai-', dir=path.parent)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
    return name


def execute(pi, arguments, timeout, environment):
    try:
        result = subprocess.run([pi] + arguments, cwd='/', env=environment,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PiCommandError(pi, arguments, environment, 'timeout') from None
    except OSError as exc:
        raise PiCommandError(pi, arguments, environment, errno.errorcode.get(exc.errno, type(exc).__name__)) from None
    if result.returncode:
        # Classify common loader errors without echoing config, tokens, or
        # arbitrary source excerpts from stderr/stdout.
        text = result.stderr + '\n' + result.stdout
        match = re.search(r'\b(ERR_[A-Z0-9_]+|MODULE_NOT_FOUND|EACCES|ENOENT)\b', text)
        reason = match[0] if match else 'unclassified launcher error'
        if re.search(r'(?:env:|exec:).*node.*(?:not found|No such file)', text, re.IGNORECASE):
            reason = 'node executable not found'
        raise PiCommandError(pi, arguments, environment, reason, result.returncode)
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
    connection = payload.get('connection')
    auth_path = path.parent / 'auth.json'
    auth_original, auth_updated = None, None
    if connection:
        parsed = urlsplit(connection['url'])
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError('Invalid direct provider URL; no changes made.')
        if auth_path.is_symlink() or (auth_path.exists() and not auth_path.is_file()):
            raise RuntimeError('Expected a regular pi auth file; no changes made.')
        auth_original = auth_path.read_bytes() if auth_path.exists() else b''
        if hashlib.sha256(auth_original).hexdigest() != payload.get('auth_revision'):
            raise RuntimeError('Pi auth changed since preflight; rerun setup with pi closed.')
        auth = json.loads(auth_original) if auth_original else {}
        if not isinstance(auth, dict):
            raise RuntimeError('Pi auth must be a JSON object.')
        entry = auth.get(connection['provider'], {})
        env = entry.get('env', {})
        # auth.json's endpoint takes precedence over provider settings. Update
        # an existing override too, preserving its credential and other fields.
        if 'LITELLM_BASE_URL' in env and env['LITELLM_BASE_URL'] != connection['url']:
            auth_updated = merge(auth, {connection['provider']: {'env': {'LITELLM_BASE_URL': connection['url']}}})
    pi = payload['pi_path']
    environment = runtime_environment(pi)
    version_output = execute(pi, ['--version'], 30, environment)
    match = re.search(r'(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)', version_output)
    if not match:
        raise RuntimeError('Could not identify the installed pi version; no changes made.')
    version = tuple(map(int, match.groups()))
    if version < MIN_PI_VERSION and not payload['upgrade_pi']:
        raise RuntimeError('This recipe requires pi >=1.4.0; allow setup to upgrade pi.')
    backup_name = backup(path, original)
    result = {'backup': backup_name, 'settings_path': str(path), 'packages': PACKAGE_VERSIONS}
    try:
        if auth_updated is not None:
            result['auth_backup'] = backup(auth_path, auth_original)
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
        if connection:
            provider_settings = {'baseUrl': connection['url']}
            if connection['url'].startswith('http:'):
                provider_settings['allowInsecureHttp'] = True
            updated = merge(updated, {'litellm': {'providers': {connection['provider']: provider_settings}}})
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
        if connection and (auth_path.read_bytes() if auth_path.exists() else b'') != auth_original:
            raise RuntimeError('Pi auth changed during setup; refusing to overwrite concurrent edits.')
        if updated != settings:
            atomic_write(path, (json.dumps(updated, indent=2) + '\n').encode())
        if auth_updated is not None:
            atomic_write(auth_path, (json.dumps(auth_updated, indent=2) + '\n').encode())
        if connection:
            result['provider_url'] = connection['url']
            result['provider'] = connection['provider']
        result['status'] = 'ok'
    except Exception as exc:
        # Package installation may already have changed settings/cache. Keep
        # the private backup; do not clobber concurrent edits with a rollback.
        result.update(failure(exc))
    return result


def apply(payload):
    try:
        return _apply(payload)
    except Exception as exc:
        return failure(exc)
