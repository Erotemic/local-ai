#!/usr/bin/env python3
"""Read-only host diagnostics for the aivm → LiteLLM → pi connection."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit

from guest_probe import models, safe_url

HERE = Path(__file__).resolve().parent
PACKAGES = ('pi-provider-litellm', 'pi-effort')


def run(argv, timeout, data=None):
    try:
        proc = subprocess.run(argv, input=data, text=True, capture_output=True,
                              timeout=timeout, env=dict(os.environ, NO_COLOR='1',
                              PYTHONDONTWRITEBYTECODE='1', LC_ALL='C'))
        return proc.returncode, proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return -1, ''


def installed_python(executable):
    """Use the installed CLI environment, including uv/pipx installations."""
    try:
        with open(executable) as stream:
            line = stream.readline().strip()
        words = shlex.split(line[2:]) if line.startswith('#!') else []
        if words and 'python' in Path(words[0]).name:
            return words[0]
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def query_aivm(args, report):
    executable = shutil.which('aivm')
    report['aivm_executable'] = executable
    interpreter = args.aivm_python or (installed_python(executable) if executable else None)
    if not interpreter:
        report['warnings'].append('Cannot find aivm Python environment; use --aivm-python /path/to/python.')
        return None
    code, output = run([interpreter, '-B', str(HERE / 'aivm_probe.py')],
                       args.timeout, json.dumps([args.aivm_config, args.vm]))
    try:
        result = json.loads(output)
    except (ValueError, TypeError):
        report['warnings'].append('aivm API probe failed; no raw command output shown (may contain secrets).')
        return None
    if code or 'error' in result:
        report['warnings'].append('aivm discovery: ' + result.get('error', 'failed') + '; ' + result.get('hint', ''))
        return None
    report['aivm'] = result
    return result


def query_stack(args, report):
    executable = shutil.which('infer-stack')
    report['infer_stack_executable'] = executable
    if not executable:
        report['warnings'].append('infer-stack is not on PATH; run this doctor on the model host.')
        return None, None
    interpreter = args.infer_stack_python or installed_python(executable)
    if not interpreter:
        report['warnings'].append('Cannot locate infer-stack Python environment; use --infer-stack-python. No unguarded CLI queries were run.')
        return None, None
    values = {}
    for name in ('OPENAI_BASE_URL', 'LITELLM_PORT', 'LITELLM_MASTER_KEY'):
        code, output = run([interpreter, '-B', str(HERE / 'stack_probe.py')],
                           args.timeout, json.dumps([executable, name]))
        value = output.strip()
        if code == 76:
            report['warnings'].append('infer-stack env ' + name + ' attempted an operation blocked by the read-only guard; query result discarded.')
            continue
        if code == 0 and value and '\n' not in value:
            values[name] = value
        elif name != 'LITELLM_PORT':
            report['warnings'].append('infer-stack env ' + name + ' did not return one value.')
    url = values.get('OPENAI_BASE_URL')
    key = values.get('LITELLM_MASTER_KEY')
    report['infer_stack'] = {'url': safe_url(url) if url else None,
                             'port_query': values.get('LITELLM_PORT'),
                             'key_available': bool(key)}
    try:
        parsed = urlsplit(url or '')
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid gateway URL')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if values.get('LITELLM_PORT') and int(values['LITELLM_PORT']) != port:
            report['warnings'].append('LITELLM_PORT disagrees with OPENAI_BASE_URL; URL port takes precedence.')
    except (ValueError, TypeError):
        report['warnings'].append('infer-stack returned an unsupported gateway URL/port; endpoint checks skipped.')
        return None, None
    report['host_gateway'] = models(url, key, args.timeout) if key else {'status': 'missing key'}
    if report['host_gateway']['status'] != 'ok':
        report['warnings'].append('Host authenticated model discovery: ' + report['host_gateway']['status'])
    return url, key


def host_local_url(url):
    hostname = urlsplit(url).hostname
    try:
        return ipaddress.ip_address(hostname).is_loopback or ipaddress.ip_address(hostname).is_unspecified
    except ValueError:
        return hostname == 'localhost'


def desired_url(url, gateway):
    parsed = urlsplit(url)
    hostname = gateway if host_local_url(url) else parsed.hostname
    if not hostname:
        raise ValueError('no VM gateway address')
    host = '[' + hostname + ']' if ':' in hostname else hostname
    netloc = host + (':' + str(parsed.port) if parsed.port else '')
    path = parsed.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3]
    return urlunsplit((parsed.scheme, netloc, path, '', ''))


def query_live_network(args, vm, report):
    """Inspect system libvirt without applying policy or requesting sudo."""
    prefix = ['virsh', '--readonly', '-c', 'qemu:///system']
    name = vm['network']['name']
    code, info = run(prefix + ['net-info', name], args.timeout)
    if code or not any(line.split(':', 1)[0].strip() == 'Active' and line.split(':', 1)[1].strip() == 'yes'
                       for line in info.splitlines() if ':' in line):
        report['notes'].append('Live libvirt network unavailable or inactive; no sudo or network activation attempted.')
        return None
    code, output = run(prefix + ['net-dumpxml', name], args.timeout)
    if code:
        report['notes'].append('Live libvirt network XML unavailable.')
        return None
    try:
        root = ET.fromstring(output)
        ipv4 = next(node for node in root.findall('ip') if node.get('family', 'ipv4') == 'ipv4')
        gateway = str(ipaddress.IPv4Address(ipv4.attrib['address']))
        subnet = str(ipaddress.IPv4Network((gateway, ipv4.get('prefix') or ipv4.attrib['netmask']), strict=False))
        bridge_node = root.find('bridge')
        result = {'name': name, 'gateway_ip': gateway, 'subnet_cidr': subnet,
                  'bridge': bridge_node.get('name') if bridge_node is not None else None}
    except (ET.ParseError, StopIteration, ValueError, KeyError):
        report['warnings'].append('Cannot parse live libvirt network IPv4 configuration.')
        return None
    report['live_network'] = result
    return result


def check_network_drift(vm, live, report):
    drift = []
    configured = vm['network']
    if live:
        for field in ('gateway_ip', 'subnet_cidr', 'bridge'):
            if live.get(field) != configured.get(field):
                drift.append('Configured ' + field + '=' + str(configured.get(field)) + '; live libvirt=' + str(live.get(field)))
    cached = vm.get('cached_ip')
    if cached:
        try:
            if ipaddress.ip_address(cached) not in ipaddress.ip_network(configured['subnet_cidr'], strict=False):
                drift.append('Cached VM address ' + cached + ' is outside configured subnet ' + configured['subnet_cidr'])
        except (ValueError, KeyError):
            drift.append('Cannot validate cached VM address against configured subnet')
    report['network_drift'] = drift
    if drift:
        report['warnings'].append('VM network configuration may be stale; endpoint/firewall changes are withheld pending reconciliation.')
    return not drift


def inspect_network(args, vm, url, report):
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    endpoint = f'{parsed.hostname}:{port}'
    firewall = vm['firewall']
    present = endpoint in firewall.get('allow_tcp_endpoints', [])
    report['connection'] = {'provider_url': url, 'endpoint': endpoint,
                            'endpoint_exception_present': present,
                            'firewall_enabled': firewall['enabled'],
                            'preview_safe': not report.get('network_drift')}
    if not present and report['connection']['preview_safe']:
        report['proposed_changes'].append('Add ' + endpoint + ' to network ' + vm['network']['name'] + ' firewall.allow_tcp_endpoints (preserve other entries).')
    if not firewall['enabled']:
        report['notes'].append('aivm firewall is disabled; preserve that setting. Endpoint exceptions are currently inactive.')
    if str(port) in {str(value) for value in firewall.get('allow_tcp_ports', [])}:
        report['notes'].append('A broad TCP port exception already covers this port; preserve it, but prefer a precise endpoint for setup.')
    report['notes'].append('Firewall configuration is desired state; live nftables enforcement is not inspected.')
    code, output = run(['ss', '-H', '-ltn', f'sport = :{port}'], args.timeout)
    if code == 0:
        report['listeners'] = [line.split()[3] for line in output.splitlines() if len(line.split()) >= 5]
        if report['listeners'] and all(v.startswith(('127.', '[::1]:')) for v in report['listeners']):
            report['warnings'].append('Visible host listeners are loopback-only. Docker DNAT may provide additional reachability; the guest probe is authoritative.')
    else:
        report['notes'].append('Host listener inspection unavailable (ss).')


def ssh_argv(args, vm, report):
    cached = vm.get('cached_ip')
    try:
        cached = str(ipaddress.ip_address(cached)) if cached else None
    except ValueError:
        cached = None
    target = args.ssh_target or cached or vm['vm']
    # No aivm ssh: it prepares mounts/firewall. Existing SSH aliases are allowed,
    # but suppress hooks, proxy commands, forwarding and connection multiplexing.
    argv = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UpdateHostKeys=no', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
            '-o', 'PermitLocalCommand=no', '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none',
            '-o', 'ForwardAgent=no', '-o', 'AddKeysToAgent=no', '-o', 'ClearAllForwardings=yes',
            '-o', f'ConnectTimeout={args.timeout}', '-o', 'ConnectionAttempts=1']
    known_hosts = vm.get('known_hosts')
    if known_hosts and Path(known_hosts).exists():
        argv += ['-o', 'UserKnownHostsFile=' + str(Path(known_hosts).expanduser())]
    identity = vm.get('identity')
    if identity:
        argv += ['-i', str(Path(identity).expanduser()), '-o', 'IdentitiesOnly=yes']
    if not args.ssh_target:
        argv += ['-l', vm['guest_user']]
    report['ssh_target'] = target
    report['ssh_address_source'] = 'explicit override' if args.ssh_target else ('cached aivm IP' if cached else 'existing VM SSH alias')
    return argv + ['--', target]


def inspect_pi(args, vm, url, key, report):
    if args.skip_guest:
        report['notes'].append('Guest checks skipped by request.')
        return
    payload = {'url': url, 'key': key, 'timeout': args.timeout, 'provider': args.provider}
    script = (HERE / 'guest_probe.py').read_text()
    argv = ssh_argv(args, vm, report) + ['python3 -B -c ' + shlex.quote(script)]
    code, output = run(argv, args.timeout * 3, json.dumps(payload))
    try:
        guest = json.loads(output)
    except ValueError:
        report['warnings'].append('Guest SSH probe failed; check existing SSH access/host keys, or use --ssh-target user@address. No SSH configuration was changed.')
        return
    if code or 'error' in guest:
        report['warnings'].append('Guest pi inspection failed: ' + guest.get('error', 'SSH failure'))
        return
    report['pi'] = guest
    connection = report.get('connection', {})
    routes = guest.get('default_routes')
    if routes and report.get('connection_gateway'):
        best = [route['gateway'] for route in routes if route['metric'] == routes[0]['metric']]
        if report['connection_gateway'] not in best:
            report.setdefault('network_drift', []).append('Guest default gateway(s) ' + ', '.join(best) + ' differ from selected host gateway ' + report['connection_gateway'])
            report['warnings'].append('Guest routing disagrees with the selected network; endpoint/firewall changes are withheld.')
            connection['preview_safe'] = False
            # Policy preview was derived before the guest routing probe.
            report['proposed_changes'] = [change for change in report['proposed_changes']
                                          if not change.startswith('Add ' + connection.get('endpoint', '') + ' to network ')]
    changes = report['proposed_changes']
    if not guest['pi_path']:
        changes.append('Provision pi through aivm before applying personal settings.')
    for package in PACKAGES:
        if not any(isinstance(p, str) and (p == 'npm:' + package or p.startswith('npm:' + package + '@')) for p in guest['packages']):
            changes.append('Install personal pi package npm:' + package + '.')
    if guest['theme'] != 'dark':
        changes.append('Set pi theme to dark.')
    provider = args.provider or guest['provider']
    if guest['defaultProvider'] != provider:
        changes.append('Select default pi provider ' + provider + '.')
    if url and connection.get('preview_safe', False) and guest['baseUrl'] != url:
        changes.append('Set pi provider URL to ' + url + ' (direct host endpoint).')
    if url and connection.get('preview_safe', False) and url.startswith('http:') and not guest['allowInsecureHttp']:
        changes.append('Enable allowInsecureHttp for this explicit non-loopback HTTP provider.')
    if guest['credential_matches'] is False:
        changes.append('Synchronize guest credential from infer-stack (values redacted).')
    elif guest['credential_matches'] is None:
        report['warnings'].append('Guest credential could not be compared without executing a credential command or missing environment; setup must resolve it.')
    model = args.model or guest['defaultModel']
    report['selected_model'] = model
    if args.model and args.model != guest['defaultModel']:
        changes.append('Set pi defaultModel to ' + args.model + '.')
    probe = guest['desired_endpoint']
    if probe['status'] != 'ok':
        report['warnings'].append('Guest → desired endpoint model discovery: ' + probe['status'])
        if probe['status'] in ('URLError', 'TimeoutError'):
            report['notes'].append('Before setup, verify the gateway is published on the VM-reachable host address and check live firewall rules; a config exception alone does not create a listener.')
    elif not model:
        report['warnings'].append('No default model selected; pass --model to preview a choice.')
    elif model not in probe['models']:
        report['warnings'].append('Selected model is absent from gateway model discovery: ' + model)
    if guest.get('current_endpoint', {}).get('status') == 'ok':
        report['notes'].append('The currently configured pi endpoint passed authenticated model discovery using the guest credential.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vm', default='', help='Override the default/single VM')
    parser.add_argument('--aivm-config', help='Existing aivm config store path')
    parser.add_argument('--aivm-python', help='Python interpreter with aivm installed (override CLI shebang discovery)')
    parser.add_argument('--infer-stack-python', help='Python interpreter for infer-stack (override CLI shebang discovery)')
    parser.add_argument('--ssh-target', help='Existing SSH alias or user@address; no SSH configuration is created')
    parser.add_argument('--provider', help='Pi provider to inspect; default: existing default/only provider')
    parser.add_argument('--model', help='Desired model; default: preserve guest selection')
    parser.add_argument('--skip-guest', action='store_true')
    parser.add_argument('--timeout', type=int, default=10, help='Per-command/network timeout in seconds')
    parser.add_argument('--json', action='store_true', help='Print a shareable report with credentials omitted')
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('--timeout must be positive')
    report = {'read_only': True, 'warnings': [], 'notes': [], 'proposed_changes': []}
    vm = query_aivm(args, report)
    url, key = query_stack(args, report)
    desired = None
    if vm and url:
        try:
            live = query_live_network(args, vm, report)
            check_network_drift(vm, live, report)
            gateway = (live or vm['network']).get('gateway_ip')
            desired = desired_url(url, gateway)
            # A pinned remote URL does not depend on the local bridge gateway.
            if host_local_url(url):
                report['connection_gateway'] = gateway
            report['endpoint_source'] = 'live libvirt network' if live else 'configured network (not verified live)'
            inspect_network(args, vm, desired, report)
        except ValueError:
            report['warnings'].append('Cannot derive guest endpoint from gateway configuration.')
    if vm:
        inspect_pi(args, vm, desired, key, report)
    if args.model and report.get('host_gateway', {}).get('status') == 'ok' and args.model not in report['host_gateway']['models']:
        report['warnings'].append('Requested model is absent from host model discovery: ' + args.model)
    if not vm or not url:
        report['notes'].append('Discovery is incomplete; the proposed change list is partial.')
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print('Pi host doctor — read only; no configuration changes or generation requests')
        for name in ('aivm_executable', 'infer_stack_executable', 'aivm', 'infer_stack', 'live_network', 'network_drift', 'endpoint_source', 'connection', 'listeners', 'ssh_target', 'ssh_address_source', 'host_gateway', 'pi'):
            if name in report:
                print('\n' + name + ':')
                print(json.dumps(report[name], indent=2))
        for name in ('proposed_changes', 'warnings', 'notes'):
            print('\n' + name.replace('_', ' ').capitalize() + ':')
            for item in report[name]:
                print('  - ' + item)
            if not report[name]:
                print('  (none)')
    # Optional diagnostics warn rather than fail bootstrap scripts.
    return 0


if __name__ == '__main__':
    sys.exit(main())
