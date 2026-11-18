"""Mocked lifecycle checks: setup owns builds; start preflights before replacement."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        service = self.root/'services/tts'
        service.mkdir(parents=True)
        self.service = service
        for name in ('start.sh','start-breeze-dual.sh','setup.sh','start-breeze-tts2.sh'):
            shutil.copy2(ROOT/'services/tts'/name, service/name)
        shutil.copy2(ROOT/'breeze.sh',self.root/'breeze.sh')
        (self.root/'.env').write_text('LOCAL_AI_SERVICE_ROOT=/tmp/mock-tts\n')
        (service/'.env').write_text('TTS_ACTIVE_BACKEND=qwentts\nTTS_BREEZE_DUAL_GPU_0=0\nTTS_BREEZE_DUAL_GPU_1=1\nTTS_BREEZE_DUAL_START_TIMEOUT=10\n')
        self.log = self.root/'calls.jsonl'
        bin_dir = self.root/'bin'
        bin_dir.mkdir()
        helper = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
command=Path(sys.argv[0]).name
with open(os.environ['MOCK_LOG'],'a') as stream:
 stream.write(json.dumps(dict(command=command,args=sys.argv[1:],backend=os.getenv('TTS_ACTIVE_BACKEND'),gpu0=os.getenv('TTS_BREEZE_DUAL_GPU_0'),gpu1=os.getenv('TTS_BREEZE_DUAL_GPU_1')))+'\\n')
if command=='docker' and os.getenv('MOCK_MISSING_IMAGE'):sys.exit(1)
if command=='curl':print(json.dumps({'workers':[{'ready':True},{'ready':True}]}))
'''
        for name in ('uv','docker','curl','nvidia-smi'):
            path=bin_dir/name
            path.write_text(helper)
            path.chmod(0o755)
        for name in ('compose.sh','start-candidate.sh'):
            path=service/name
            path.write_text(helper)
            path.chmod(0o755)
        self.env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'MOCK_LOG':str(self.log)}
        for key in list(self.env):
            if key.startswith('TTS_'):
                self.env.pop(key)

    def run_command(self,*args,**overrides):
        return subprocess.run([str(self.root/'breeze.sh'),*args],env={**self.env,**overrides},capture_output=True,text=True,timeout=15)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_setup_builds_gateway_via_the_normal_setup_entry_point(self):
        result=self.run_command('setup')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls()
        self.assertEqual(calls[0]['command'],'uv')
        self.assertIn('setup',calls[0]['args'])
        self.assertIn('--with-model',calls[0]['args'])
        self.assertIn('breeze-tts2',calls[0]['args'])
        self.assertEqual(calls[0]['backend'],'breeze-tts2-dual')
        self.assertTrue(any(c['args']==['build','breeze-gateway'] for c in calls))

    def test_setup_plan_and_no_build_do_not_build_gateway(self):
        for option in ('--plan','--no-build'):
            self.log.unlink(missing_ok=True)
            result=self.run_command('setup',option)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(any(c['command']=='compose.sh' and 'build' in c['args'] for c in self.calls()))

    def test_start_preserves_device_overrides_and_never_builds_or_downloads(self):
        result=self.run_command('start',TTS_BREEZE_DUAL_GPU_0='2',TTS_BREEZE_DUAL_GPU_1='3')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls()
        self.assertIn('check',calls[0]['args'])
        compose=[c for c in calls if c['command']=='compose.sh']
        self.assertEqual(compose[0]['args'],['config','--quiet'])
        self.assertTrue(any(c['args']==['up','-d','--no-build','--pull','never','breeze-gateway'] for c in compose))
        self.assertTrue(all(c['gpu0']=='2' and c['gpu1']=='3' for c in compose))
        self.assertFalse(any('setup' in c['args'] or 'build' in c['args'] or 'pull' in c['args'] for c in calls))
        self.assertIn('http://127.0.0.1:11441/v1',result.stdout)

    def test_bad_devices_and_missing_images_leave_running_services_untouched(self):
        result=self.run_command('start',TTS_BREEZE_DUAL_GPU_0='0',TTS_BREEZE_DUAL_GPU_1='00')
        self.assertEqual(result.returncode,2)
        self.assertEqual(self.calls(),[])
        result=self.run_command('start',MOCK_MISSING_IMAGE='1')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any(c['command']=='compose.sh' and c['args'][0] in ('stop','up') for c in self.calls()))

    def test_keep_running_replaces_only_conflicting_single_gpu_breeze(self):
        result=self.run_command('start','--keep-running')
        self.assertEqual(result.returncode,0,result.stderr)
        stops=[c['args'] for c in self.calls() if c['command']=='compose.sh' and c['args'][0]=='stop']
        self.assertEqual(stops,[['stop','breeze-tts2']])

    def test_single_gpu_rollback_stops_dual_replicas(self):
        result=self.run_command('single')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls()
        self.assertTrue(any(c['command']=='compose.sh' and 'breeze-gateway' in c['args'] and 'breeze-worker-1' in c['args'] for c in calls))
        self.assertTrue(any(c['command']=='start-candidate.sh' and c['args']==['breeze-tts2'] for c in calls))


@unittest.skipUnless(shutil.which('docker'), 'Docker Compose is optional for offline model tests')
class ComposeTests(unittest.TestCase):
    def test_resolved_workers_are_private_distinct_and_share_the_existing_model(self):
        env={**os.environ, 'HF_REPOS_ROOT':'/tmp/mock-models', 'TTS_DATA_ROOT':'/tmp/mock-tts',
             'TTS_BREEZE_DUAL_GPU_0':'2','TTS_BREEZE_DUAL_GPU_1':'3',
             'TTS_BREEZE_FAST':'1','TTS_BREEZE_PORT':'12441','TTS_BIND_ADDRESS':'127.0.0.1'}
        args=['docker','compose','--env-file','.env.template','--env-file','services/tts/.env.template',
              '-f','services/tts/compose.yaml','--profile','breeze-dual','config','--format','json']
        services=json.loads(subprocess.check_output(args,cwd=ROOT,env=env))['services']
        for worker,gpu in [('breeze-worker-0','2'),('breeze-worker-1','3')]:
            data=services[worker]
            self.assertFalse(data.get('ports'))
            self.assertEqual(data['deploy']['resources']['reservations']['devices'][0]['device_ids'],[gpu])
            self.assertEqual(data['environment']['TTS_BREEZE_FAST'],'1')
            self.assertEqual(data['image'],services['breeze-tts2']['image'])
            self.assertTrue(next(volume for volume in data['volumes'] if volume['target']=='/models')['read_only'])
        self.assertEqual(services['breeze-gateway']['ports'][0]['published'],'12441')
        self.assertEqual(services['breeze-gateway']['ports'][0]['host_ip'],'127.0.0.1')
        self.assertEqual(set(services['breeze-gateway']['depends_on']),{'breeze-worker-0','breeze-worker-1'})


if __name__=='__main__':
    unittest.main()
