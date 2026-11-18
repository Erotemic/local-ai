#!/usr/bin/env python3
"""Render and operate the Breeze replica pool selected by TTS_BREEZE_GPUS.

Compose configuration is rendered in memory. Start never writes configuration,
builds images, provisions models, or changes the saved GPU selection.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.request import urlopen
import time

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]


def read_env(path):
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=(.*)$', line)
            if match:
                tokens = shlex.split(match[2], comments=True)
                result[match[1]] = ' '.join(tokens)
    return result


def gpu_list(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Set TTS_BREEZE_GPUS in services/tts/.env: 0 for one GPU, 0,1 for two, or an explicit list for N GPUs. Run ./breeze.sh setup to update older configuration.')
    parts = value.split(',')
    if any(not re.fullmatch(r'\d+', part.strip(), flags=re.ASCII) for part in parts):
        raise ValueError('TTS_BREEZE_GPUS must be a comma-separated list of nonnegative physical GPU indexes')
    result = [int(part.strip()) for part in parts]
    if len(set(result)) != len(result):
        raise ValueError('TTS_BREEZE_GPUS contains duplicate GPU indexes')
    return result


def environment(service=SERVICE):
    root = service.parents[1]
    if not (root/'.env').is_file() or not (service/'.env').is_file():
        raise ValueError('Run ./breeze.sh setup first to create service configuration')
    settings = {**read_env(root/'.env'), **read_env(service/'.env')}
    settings.update({key:value for key,value in os.environ.items() if key.startswith('TTS_')})
    settings['TTS_DATA_ROOT'] = settings.get('TTS_DATA_ROOT') or settings.get('LOCAL_AI_SERVICE_ROOT', '').rstrip('/') + '/tts'
    return {**os.environ, **settings}


def topology(base, env):
    """One private worker per selected physical GPU; one stable client endpoint."""
    gpus = gpu_list(env.get('TTS_BREEZE_GPUS'))
    result = deepcopy(base)
    services = result['services']
    template = services['breeze-tts2']
    timeout = int(env.get('TTS_BREEZE_START_TIMEOUT', '1800'))
    if timeout <= 0:
        raise ValueError('TTS_BREEZE_START_TIMEOUT must be positive')
    names = []
    for gpu in gpus:
        name = f'breeze-worker-{gpu}'
        names.append(name)
        worker = deepcopy(template)
        worker.update(container_name='ai-voice-'+name, profiles=['breeze'], ports=[])
        worker['deploy'] = {'resources': {'reservations': {'devices': [dict(driver='nvidia', device_ids=[str(gpu)], capabilities=['gpu'])]}}}
        worker['healthcheck'].update(interval='10s', start_period=f'{timeout}s')
        worker.setdefault('labels', {})['com.local-ai.breeze.gpu'] = str(gpu)
        services[name] = worker
    services['breeze-gateway'] = {
        'profiles':['breeze'], 'image':'local/tts-breeze-gateway:py313',
        'build':{'context':template['build']['context'], 'dockerfile':'services/tts/Dockerfile.breeze-gateway'},
        'container_name':'ai-voice-breeze-gateway', 'restart':'unless-stopped',
        'depends_on':{name:{'condition':'service_healthy'} for name in names},
        'ports':deepcopy(template.get('ports', [])),
        'environment':{'BREEZE_GATEWAY_HOST':'0.0.0.0', 'BREEZE_GATEWAY_DB':'/state/routes.sqlite3',
            'BREEZE_GATEWAY_TIMEOUT':env.get('TTS_BREEZE_GATEWAY_TIMEOUT','1800'),
            'BREEZE_GPU_IDS':','.join(map(str,gpus)),
            'BREEZE_WORKERS':','.join(f'http://{name}:8080' for name in names), 'PYTHONUNBUFFERED':'1'},
        'volumes':[{'type':'bind','source':str(Path(env['TTS_DATA_ROOT'])/'breeze-gateway'),'target':'/state','read_only':False}],
        'healthcheck':{'test':['CMD','python','-c',"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=10).read()"], 'interval':'30s','timeout':'12s','retries':3},
    }
    return result


def render(service=SERVICE, env=None):
    env = env or environment(service)
    gpu_list(env.get('TTS_BREEZE_GPUS'))  # Validate before invoking Docker.
    base = json.loads(subprocess.check_output([str(service/'compose.sh'),'--profile','breeze-image','config','--format','json'],env=env))
    return topology(base, env)


def compose(data, args, env, service=SERVICE):
    return subprocess.run(['docker','compose','--project-directory',str(service),'--profile','breeze','-f','-',*args],
                          input=json.dumps(data), text=True, env=env, check=True)


def containers(env, service=SERVICE):
    # Include retired pool members so reducing the list releases their GPUs.
    output = subprocess.check_output(['docker','ps','-a','--filter',f'label=com.docker.compose.project={service.name}',
        '--format','{{.ID}} {{.Label "com.docker.compose.service"}}'],text=True,env=env)
    result = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields)==2 and re.fullmatch('[0-9a-f]+',fields[0]) and re.fullmatch(r'breeze-(?:worker-\d+|gateway|tts2)',fields[1]):
            result.append(tuple(fields))
    return result


def summary(env):
    gpus = gpu_list(env.get('TTS_BREEZE_GPUS'))
    print(f"Breeze GPU configuration: TTS_BREEZE_GPUS={','.join(map(str,gpus))} ({len(gpus)} GPU{'s' if len(gpus)!=1 else ''}, {len(gpus)} replica{'s' if len(gpus)!=1 else ''}); fast={env.get('TTS_BREEZE_FAST','0')}", flush=True)
    return gpus


def migrate(path):
    """Setup-only migration; an explicit new list always wins over legacy fields."""
    if not path.exists():
        return
    values = read_env(path)
    lines = path.read_text().splitlines()
    changed = False
    if not values.get('TTS_BREEZE_GPUS') and ('TTS_BREEZE_GPU' in values or 'TTS_BREEZE_DUAL_GPU_0' in values):
        if values.get('TTS_ACTIVE_BACKEND')=='breeze-tts2-dual':
            selection=values.get('TTS_BREEZE_DUAL_GPU_0','0')+','+values.get('TTS_BREEZE_DUAL_GPU_1','1')
        else:
            selection=values.get('TTS_BREEZE_GPU','0')
        gpu_list(selection)
        lines += ['', '# Explicit Breeze GPU selection; one model replica per listed card.', 'TTS_BREEZE_GPUS='+selection]
        print(f'Migrating Breeze GPU configuration to TTS_BREEZE_GPUS={selection}. Review this list in setup to select 1, 2, or N GPUs.',flush=True)
        changed=True
    obsolete={'TTS_BREEZE_GPU','TTS_BREEZE_DUAL_GPU_0','TTS_BREEZE_DUAL_GPU_1','TTS_BREEZE_DUAL_START_TIMEOUT'}
    output=[]
    for line in lines:
        match=re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=',line)
        if match and match[1] in obsolete:
            changed=True
            continue
        if match and match[1]=='TTS_ACTIVE_BACKEND' and values.get('TTS_ACTIVE_BACKEND')=='breeze-tts2-dual':
            line='TTS_ACTIVE_BACKEND=breeze-tts2'
            changed=True
        output.append(line)
    if changed:
        # Same-directory atomic replacement preserves the existing private mode.
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name)
            stream.write('\n'.join(output)+'\n')
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)


def main(args=None):
    args=list(sys.argv[1:] if args is None else args)
    action=args.pop(0) if args else 'config'
    if action=='migrate':
        migrate(SERVICE/'.env')
        return
    env=environment()
    if action=='stop':
        if env.get('TTS_BREEZE_GPUS'):
            summary(env)
        ids=[identifier for identifier,_ in containers(env)]
        if ids:
            subprocess.run(['docker','stop',*ids],env=env,check=True)
        return
    gpus=summary(env)
    if action=='config':
        return
    if action=='status':
        subprocess.run(['docker','ps','-a','--filter',f'label=com.docker.compose.project={SERVICE.name}',
            '--filter','name=ai-voice-breeze','--format','table {{.Names}}\t{{.Status}}\t{{.Ports}}'],env=env,check=True)
        return
    data=render(env=env)
    if action=='build':
        compose(data,['build','breeze-gateway'],env)
    elif action=='logs':
        compose(data,['logs','--tail=100','-f','breeze-gateway',*[f'breeze-worker-{gpu}' for gpu in gpus]],env)
    elif action=='start':
        stop_others=args==['--stop-other-backends']
        if args and not stop_others:
            raise ValueError('start accepts only --stop-other-backends')
        for image in ('local/tts-breeze-tts2:candidate','local/tts-breeze-gateway:py313'):
            if subprocess.run(['docker','image','inspect',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env).returncode:
                raise ValueError(f'Image {image} is missing. Run ./breeze.sh setup first.')
        subprocess.run(['uv','run',str(ROOT/'scripts/local_ai.py'),'model','check','breeze-tts2','--service-dir',str(SERVICE)],env=env,check=True)
        # Rendering config validates it with Compose before any running server is stopped.
        compose(data,['config','--quiet'],env)
        names={f'breeze-worker-{gpu}' for gpu in gpus}
        retired=[identifier for identifier,name in containers(env) if name=='breeze-tts2' or (name.startswith('breeze-worker-') and name not in names)]
        if retired:
            subprocess.run(['docker','stop',*retired],env=env,check=True)
        if stop_others:
            subprocess.run([str(SERVICE/'compose.sh'),'stop','qwentts-gateway','qwentts','kokoro-gpu','kokoro-cpu','indextts25','chatterbox-flash','chatterbox-nano','fish-s2-pro'],env=env,check=True)
        compose(data,['up','-d','--no-build','--pull','never','breeze-gateway'],env)
        host=env.get('TTS_BIND_ADDRESS','127.0.0.1')
        if host in ('0.0.0.0','::','[::]'):host='127.0.0.1'
        if ':' in host and not host.startswith('['):host='['+host+']'
        base=f"http://{host}:{env.get('TTS_BREEZE_PORT','11441')}"
        deadline=time.monotonic()+int(env.get('TTS_BREEZE_START_TIMEOUT','1800'))
        while time.monotonic()<deadline:
            try:
                with urlopen(base+'/health',timeout=10) as response:
                    info=json.load(response)
                if len(info.get('workers',[]))==len(gpus) and all(worker['ready'] for worker in info['workers']):
                    print(f'Breeze ready: {len(gpus)} replica(s), GPUs {gpus}, {base}/v1')
                    return
            except (OSError,ValueError):
                pass
            time.sleep(3)
        raise ValueError('Not all configured Breeze workers became ready. Inspect ./breeze.sh logs.')
    else:
        raise ValueError('Expected config, start, stop, status, logs, or build')


if __name__=='__main__':
    try:
        main()
    except (ValueError,subprocess.CalledProcessError) as error:
        print('ERROR: '+str(error),file=sys.stderr)
        sys.exit(2)
