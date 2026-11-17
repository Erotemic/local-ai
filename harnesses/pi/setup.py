#!/usr/bin/env python3
"""Configure an existing VM's pi preferences from the host."""
import json
import shlex
import sys

import doctor


def remote_source():
    """Load the shared policy in memory; no helper files needed in the VM."""
    policy = (doctor.HERE / 'preferences.py').read_text()
    setup = (doctor.HERE / 'guest_setup.py').read_text()
    return ('import types,sys,json\n'
            'policy=types.ModuleType("preferences")\n'
            'exec(' + repr(policy) + ',policy.__dict__)\n'
            'sys.modules["preferences"]=policy\n' + setup + '\n'
            'result=apply(json.load(sys.stdin))\n'
            'print(json.dumps(result))\n'
            'sys.exit(0 if result["status"]=="ok" else 1)\n')


def main():
    parser = doctor.build_parser()
    parser.description = __doc__
    parser.add_argument('--dry-run', action='store_true', help='Read-only preflight and change preview')
    parser.add_argument('--no-upgrade-pi', action='store_true', help='Fail instead of upgrading pi below the tested 1.4.0 baseline')
    parser.add_argument('--install-timeout', type=int, default=300, help='Timeout per pi upgrade/package install in seconds')
    args = parser.parse_args()
    if args.skip_guest or args.timeout < 1 or args.install_timeout < 1:
        parser.error('Setup needs guest inspection and positive timeouts.')
    report, _, _ = doctor.collect(args)
    if args.dry_run:
        print(json.dumps(report, indent=2))
        return 0
    guest = report.get('pi', {})
    connection = report.get('connection', {})
    model = report.get('selected_model')
    levels = report.get('model_capabilities', {}).get('reasoning_effort_levels', [])
    effort = args.effort or doctor.BASELINE['defaultThinkingLevel']
    reasons = []
    if not connection.get('preview_safe') or not guest.get('pi_path'):
        reasons.append('VM/pi discovery must succeed without network drift.')
    if guest.get('desired_endpoint', {}).get('status') != 'ok' or model not in guest.get('desired_endpoint', {}).get('models', []):
        reasons.append('The selected model must be discoverable from the guest.')
    if guest.get('credential_matches') is not True or guest.get('baseUrl') != connection.get('provider_url'):
        reasons.append('This setup step requires the existing guest endpoint and credential to match infer-stack; connection changes are not applied here.')
    if reasons:
        print(json.dumps({'status': 'blocked', 'reasons': reasons, 'doctor': report}, indent=2))
        return 1
    payload = {name: guest[name] for name in ('settings_path', 'settings_revision', 'pi_path')}
    payload.update(preferences=report['desired_preferences'], upgrade_pi=not args.no_upgrade_pi,
                   install_timeout=args.install_timeout)
    # Ship code through SSH; no checkout or helper installation in the VM.
    argv = doctor.ssh_argv(args, report['aivm'], report) + ['python3 -B -c ' + shlex.quote(remote_source())]
    code, output = doctor.run(argv, args.install_timeout * 3 + 90, json.dumps(payload))
    try:
        result = json.loads(output)
    except ValueError:
        print('Guest setup failed or disconnected. Settings/package changes may be partial; inspect settings.json.before-local-ai-* in the guest agent directory.')
        return 1
    result['effort'] = {
        'requested': effort,
        'metadata_verified': effort in levels and bool(report.get('model_capabilities', {}).get('supports_reasoning')),
        'advertised_levels': levels,
    }
    result['warnings'] = report.get('warnings', [])
    print(json.dumps(result, indent=2))
    if code or result.get('status') != 'ok':
        return 1
    if not args.json:
        print('Local pi preferences saved. Start a fresh pi session; run /litellm-refresh, then /effort to check available choices. Saved effort preferences do not establish runtime support.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
