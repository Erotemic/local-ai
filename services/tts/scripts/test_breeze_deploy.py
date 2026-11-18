"""GPU selection, scalable Compose rendering, migration and launch preflight."""
from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import breeze_deploy as deploy

ROOT=Path(__file__).resolve().parents[3]


def base():
    return {'name':'tts','services':{'breeze-tts2':{
        'image':'local/tts-breeze-tts2:candidate','build':{'context':str(ROOT),'dockerfile':'services/tts/Dockerfile.breeze'},
        'profiles':['breeze-image'],'environment':{'TTS_BREEZE_FAST':'1'},
        'volumes':[{'type':'bind','source':'/models','target':'/models','read_only':True}],
        'ports':[{'target':8080,'published':'8821','host_ip':'127.0.0.1'}],
        'healthcheck':{'test':['CMD','true'],'interval':'30s'},
    }}}


class ConfigurationTests(unittest.TestCase):
    def env(self,gpus='0'):
        return {'TTS_BREEZE_GPUS':gpus,'TTS_DATA_ROOT':'/tmp/test-tts','TTS_BREEZE_START_TIMEOUT':'1800'}

    def test_one_two_four_and_noncontiguous_gpu_selections(self):
        original=base();before=deepcopy(original)
        for selected in ('0','0,1','0,1,2,3','3,7,12'):
            with self.subTest(selected=selected):
                data=deploy.topology(original,self.env(selected));gpus=deploy.gpu_list(selected)
                workers={k:v for k,v in data['services'].items() if k.startswith('breeze-worker-')}
                self.assertEqual(len(workers),len(gpus))
                for gpu in gpus:
                    worker=workers[f'breeze-worker-{gpu}']
                    self.assertFalse(worker['ports'])
                    self.assertEqual(worker['deploy']['resources']['reservations']['devices'][0]['device_ids'],[str(gpu)])
                    self.assertEqual(worker['image'],original['services']['breeze-tts2']['image'])
                    self.assertEqual(worker['environment']['TTS_BREEZE_FAST'],'1')
                    self.assertTrue(worker['volumes'][0]['read_only'])
                gateway=data['services']['breeze-gateway']
                self.assertEqual(len(gateway['depends_on']),len(gpus))
                self.assertEqual(gateway['environment']['BREEZE_GPU_IDS'],selected)
                self.assertEqual(gateway['ports'],original['services']['breeze-tts2']['ports'])
        self.assertEqual(original,before)

    def test_selection_is_explicit_and_duplicate_or_empty_devices_fail(self):
        self.assertEqual(deploy.gpu_list(' 0, 02, 7 '),[0,2,7])
        for invalid in (None,'',' ','all','auto','-1','0,','0,,1','0,00','0,1,0','GPU-x'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                deploy.gpu_list(invalid)

    def test_setup_migration_preserves_explicit_selection_and_private_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'.env'
            old='TTS_ACTIVE_BACKEND=breeze-tts2-dual\nTTS_BREEZE_PORT=8821\nTTS_BREEZE_FAST=1\nTTS_BREEZE_DUAL_GPU_0=2\nTTS_BREEZE_DUAL_GPU_1=3\nTTS_BREEZE_GPU=0\n'
            path.write_text(old);path.chmod(0o600)
            deploy.migrate(path)
            settings=deploy.read_env(path)
            self.assertEqual(settings['TTS_BREEZE_GPUS'],'2,3')
            self.assertEqual(settings['TTS_BREEZE_PORT'],'8821')
            self.assertEqual(settings['TTS_BREEZE_FAST'],'1')
            self.assertEqual(settings['TTS_ACTIVE_BACKEND'],'breeze-tts2')
            self.assertNotIn('TTS_BREEZE_GPU',settings)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            before=path.read_bytes();deploy.migrate(path);self.assertEqual(path.read_bytes(),before)
            path.write_text(old+'TTS_BREEZE_GPUS=7\n');deploy.migrate(path)
            self.assertEqual(deploy.read_env(path)['TTS_BREEZE_GPUS'],'7')

    def test_start_never_stops_services_when_preflight_fails(self):
        env=self.env('0,1,2,3')
        with patch.object(deploy,'environment',return_value=env),patch.object(deploy,'render',return_value=deploy.topology(base(),env)),patch.object(deploy.subprocess,'run') as run:
            run.return_value.returncode=1
            with self.assertRaises(ValueError):deploy.main(['start'])
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0][:3],['docker','image','inspect'])

    def test_start_respects_n_devices_and_only_retires_removed_workers(self):
        env=self.env('0,2,3,7');env.update(TTS_BREEZE_PORT='8821',TTS_BIND_ADDRESS='127.0.0.1')
        health=io.StringIO(json.dumps({'workers':[{'ready':True} for _ in range(4)]}))
        with patch.object(deploy,'environment',return_value=env),patch.object(deploy,'render',return_value=deploy.topology(base(),env)),patch.object(deploy,'compose') as compose,patch.object(deploy,'containers',return_value=[('a1','breeze-worker-0'),('b2','breeze-worker-1'),('c3','breeze-tts2')]),patch.object(deploy.subprocess,'run') as run,patch.object(deploy,'urlopen',return_value=contextlib.nullcontext(health)):
            run.return_value.returncode=0
            deploy.main(['start'])
            commands=[call.args[0] for call in run.call_args_list]
            self.assertIn(['docker','stop','b2','c3'],commands)
            self.assertFalse(any('build' in command or 'setup' in command for command in commands))
            self.assertTrue(any(call.args[1]==['up','-d','--no-build','--pull','never','breeze-gateway'] for call in compose.call_args_list))

    def test_config_and_start_read_same_saved_selection_and_allow_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            service=Path(folder)/'services/tts';service.mkdir(parents=True)
            (Path(folder)/'.env').write_text('LOCAL_AI_SERVICE_ROOT=/data/local-ai\n')
            (service/'.env').write_text('TTS_BREEZE_GPUS=0,1,2,3\nTTS_BREEZE_PORT=8821\n')
            with patch.dict(os.environ,{},clear=True):
                self.assertEqual(deploy.gpu_list(deploy.environment(service)['TTS_BREEZE_GPUS']),[0,1,2,3])
            with patch.dict(os.environ,{'TTS_BREEZE_GPUS':'2'},clear=True):
                self.assertEqual(deploy.environment(service)['TTS_BREEZE_GPUS'],'2')


@unittest.skipUnless(shutil.which('docker'),'Docker Compose unavailable')
class ComposeTests(unittest.TestCase):
    def test_actual_compose_accepts_one_two_and_four_replicas(self):
        env={**os.environ,'HF_REPOS_ROOT':'/tmp/mock-models','TTS_DATA_ROOT':'/tmp/mock-tts'}
        args=['docker','compose','--env-file','.env.template','--env-file','services/tts/.env.template','-f','services/tts/compose.yaml','--profile','breeze-image','config','--format','json']
        original=json.loads(subprocess.check_output(args,cwd=ROOT,env=env))
        for selection in ('0','0,1','0,1,2,3'):
            config=deploy.topology(original,{'TTS_BREEZE_GPUS':selection,'TTS_DATA_ROOT':'/tmp/mock-tts'})
            result=subprocess.run(['docker','compose','--profile','breeze','--project-directory',str(ROOT/'services/tts'),'-f','-','config','--format','json'],input=json.dumps(config),text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            resolved=json.loads(result.stdout)['services']
            self.assertEqual(len([k for k in resolved if k.startswith('breeze-worker-')]),len(deploy.gpu_list(selection)))
            self.assertNotIn('breeze-tts2',resolved) # Image template cannot compete for the public port.


if __name__=='__main__':unittest.main()
