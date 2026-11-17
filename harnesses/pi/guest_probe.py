"""Sent through SSH; reports only selected, non-secret pi fields."""
import json
import errno
import os
from pathlib import Path
import shutil
import sys
import socket
import struct
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def models(url, key, timeout):
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
    request = urllib.request.Request(base + '/v1/models', headers={'Authorization': 'Bearer ' + key})
    try:
        with opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read(4 * 1024 * 1024))
        return {'status': 'ok', 'models': [v['id'] for v in data['data'] if isinstance(v.get('id'), str)]}
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
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    auth_path = root / 'auth.json'
    auth = json.loads(auth_path.read_text()) if auth_path.exists() else {}
    providers = settings.get('litellm', {}).get('providers', {})
    provider = payload.get('provider') or settings.get('defaultProvider')
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
    result = {
        'settings_path': str(settings_path), 'settings_exists': settings_path.exists(),
        'pi_path': shutil.which('pi') or (str(root / 'bin/pi') if (root / 'bin/pi').exists() else None),
        'theme': settings.get('theme'), 'defaultProvider': settings.get('defaultProvider'),
        'defaultModel': settings.get('defaultModel'), 'provider': provider,
        'baseUrl': safe_url(url) if url else None,
        'allowInsecureHttp': config.get('allowInsecureHttp', False),
        'packages': [p.get('source') if isinstance(p, dict) else p for p in packages],
        'credential_source': source,
        'credential_matches': key == payload['key'] if key and payload['key'] else None,
        'default_routes': default_routes(),
        'desired_endpoint': models(payload['url'], payload['key'], payload['timeout']) if payload['url'] and payload['key'] else {'status': 'not checked'},
    }
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
