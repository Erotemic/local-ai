"""Sent through SSH; reports only selected, non-secret pi fields."""
import json
import errno
import hashlib
import os
from pathlib import Path
import shutil
import sys
import socket
import struct
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def gateway_json(url, key, timeout, path):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        return {'status': 'invalid URL'}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    base = url.rstrip('/')
    if base.endswith('/v1'):
        base = base[:-3]
    request = urllib.request.Request(base + path, headers={'Authorization': 'Bearer ' + key})
    try:
        with opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read(4 * 1024 * 1024))
        return {'status': 'ok', 'data': data}
    except urllib.error.HTTPError as exc:
        return {'status': 'HTTP ' + str(exc.code)}
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            detail = 'DNS resolution failed'
        elif isinstance(reason, TimeoutError):
            detail = 'connection timed out'
        else:
            detail = errno.errorcode.get(getattr(reason, 'errno', None), type(reason).__name__)
        return {'status': 'URLError', 'reason': detail}
    except Exception as exc:
        return {'status': type(exc).__name__}


def models(url, key, timeout):
    result = gateway_json(url, key, timeout, '/v1/models')
    if result['status'] != 'ok':
        return result
    try:
        return {'status': 'ok', 'models': [v['id'] for v in result['data']['data'] if isinstance(v.get('id'), str)]}
    except (KeyError, TypeError, AttributeError):
        return {'status': 'invalid model-list response'}


def capabilities(url, key, timeout, model):
    for path in ('/model/info', '/v1/model/info'):
        result = gateway_json(url, key, timeout, path)
        if result['status'] != 'ok':
            continue
        data = result['data']
        entries = data.get('data') if isinstance(data, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get('model_name') != model:
                continue
            info = entry.get('model_info') or {}
            if not isinstance(info, dict):
                continue
            levels = info.get('reasoning_effort_levels')
            return {'status': 'ok', 'model': model,
                    'reasoning_effort_levels': [level for level in levels if level in ('low', 'medium', 'high', 'minimal', 'xhigh', 'max', 'off')] if isinstance(levels, list) else [],
                    'supports_reasoning': info.get('supports_reasoning') is True}
    return {'status': 'effort metadata unavailable', 'model': model, 'reasoning_effort_levels': []}


def safe_url(value):
    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not p.hostname:
            return '(invalid URL)'
        host = '[' + p.hostname + ']' if ':' in p.hostname else p.hostname
        return p.scheme + '://' + host + (':' + str(p.port) if p.port else '') + p.path
    except (ValueError, TypeError):
        return '(invalid URL)'


def default_routes(path=Path('/proc/net/route')):
    """Read Linux IPv4 default routes without running guest commands."""
    routes = []
    try:
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) < 8 or fields[1] != '00000000' or fields[7] != '00000000':
                continue
            flags = int(fields[3], 16)
            if flags & 3 != 3:
                continue
            gateway = socket.inet_ntoa(struct.pack('<I', int(fields[2], 16)))
            routes.append({'interface': fields[0], 'gateway': gateway, 'metric': int(fields[6])})
    except (OSError, ValueError):
        return None
    return sorted(routes, key=lambda row: row['metric'])


def inspect_guest(payload):
    root = Path(os.environ.get('PI_CODING_AGENT_DIR') or Path.home() / '.pi/agent')
    settings_path = root / 'settings.json'
    settings_bytes = settings_path.read_bytes() if settings_path.exists() else b''
    settings = json.loads(settings_bytes) if settings_bytes else {}
    auth_path = root / 'auth.json'
    auth_bytes = auth_path.read_bytes() if auth_path.exists() else b''
    auth = json.loads(auth_bytes) if auth_bytes else {}
    providers = settings.get('litellm', {}).get('providers', {})
    provider = payload.get('provider')
    if not provider:
        provider = settings.get('defaultProvider')
        if not provider or provider not in providers:
            provider = next(iter(providers)) if len(providers) == 1 else (provider or 'litellm')
    config = providers.get(provider, {})
    credentials = auth.get(provider, {})
    key = credentials.get('key') or config.get('apiKey')
    source = 'auth.json' if credentials.get('key') else 'settings.json'
    if isinstance(key, str) and key.startswith('$'):
        key = os.environ.get(key[1:])
        source += ' environment reference'
    elif isinstance(key, str) and key.startswith('!'):
        key = None
        source += ' command reference (not executed)'
    elif not key:
        key = os.environ.get('LITELLM_API_KEY')
        source = 'environment or unavailable'
    url = credentials.get('env', {}).get('LITELLM_BASE_URL') or config.get('baseUrl') or os.environ.get('LITELLM_BASE_URL')
    packages = settings.get('packages', [])
    # Only report recipe-owned settings; never return the full settings object.
    desired = dict(payload.get('preferences', {}))
    model = payload.get('model') or settings.get('defaultModel')
    if model and desired:
        model_key = provider + '/' + model
        level = desired['defaultThinkingLevel']
        desired['modelThinkingLevels'] = {model_key: level}
        desired['pi-effort'] = {'modelEffort': {model_key: level}}
    def selected(current, template):
        return {name: selected(current.get(name, {}) if isinstance(current.get(name), dict) else {}, value)
                if isinstance(value, dict) else current.get(name)
                for name, value in template.items()}
    subagent_path = root / 'extensions/subagent/config.json'
    subagent_bytes = subagent_path.read_bytes() if subagent_path.exists() else b''
    subagent_config = json.loads(subagent_bytes) if subagent_bytes else {}
    result = {
        'settings_path': str(settings_path), 'settings_exists': settings_path.exists(),
        'settings_revision': hashlib.sha256(settings_bytes).hexdigest(),
        'auth_revision': hashlib.sha256(auth_bytes).hexdigest(),
        'pi_path': shutil.which('pi') or (str(root / 'bin/pi') if (root / 'bin/pi').exists() else None),
        'theme': settings.get('theme'), 'defaultProvider': settings.get('defaultProvider'),
        'defaultModel': settings.get('defaultModel'), 'provider': provider,
        'baseUrl': safe_url(url) if url else None,
        'allowInsecureHttp': config.get('allowInsecureHttp', False),
        'packages': [p.get('source') if isinstance(p, dict) else p for p in packages],
        'credential_source': source,
        'credential_matches': key == payload['key'] if key and payload['key'] else None,
        'default_routes': default_routes(),
        'preferences': selected(settings, desired),
        'subagent_config': selected(subagent_config, payload.get('subagent_config', {})),
        'subagent_config_revision': hashlib.sha256(subagent_bytes).hexdigest(),
        'package_versions': {},
        'pi_version': None,
        'desired_endpoint': models(payload['url'], payload['key'], payload['timeout']) if payload['url'] and payload['key'] else {'status': 'not checked'},
    }
    # Inspect managed/npm metadata without launching pi in the read-only doctor.
    version_paths = [Path.home() / '.local/share/pi-node/current/lib/node_modules/@earendil-works/pi-coding-agent/package.json']
    marker = root / 'install/current-version'
    if marker.exists():
        release = marker.read_text().strip()
        if release and '/' not in release and release not in ('.', '..'):
            version_paths.insert(0, root / 'install/releases' / release / 'node_modules/@earendil-works/pi-coding-agent/package.json')
    executable = result['pi_path']
    if executable:
        version_paths.extend(parent / 'package.json' for parent in Path(executable).resolve().parents)
    for path in version_paths:
        try:
            metadata = json.loads(path.read_text())
            if metadata.get('name') == '@earendil-works/pi-coding-agent':
                result['pi_version'] = metadata['version']
                break
        except (OSError, ValueError, KeyError):
            pass
    for package in payload.get('package_names', ('pi-provider-litellm', 'pi-effort', 'pi-subagents', 'context-mode', 'pi-web-access', 'pi-goal-list-loop-audit')):
        metadata = root / 'npm/node_modules' / package / 'package.json'
        try:
            result['package_versions'][package] = json.loads(metadata.read_text())['version']
        except (OSError, ValueError, KeyError):
            result['package_versions'][package] = None
    # Test existing pi configuration independently; do not send the host key to
    # an arbitrary configured URL. Only the guest's own resolved credential.
    result['current_endpoint'] = models(url, key, payload['timeout']) if url and key else {'status': 'not checked'}
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(inspect_guest(json.load(sys.stdin))))
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__}))
        sys.exit(1)
