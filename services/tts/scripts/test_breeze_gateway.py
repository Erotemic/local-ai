"""CPU-only HTTP integration tests for the N-GPU gateway contract."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from breeze_gateway import Gateway, Handler


def call(base, method, path, value=None):
    data = json.dumps(value).encode() if value is not None else None
    try:
        with urlopen(Request(base+path, data=data, method=method), timeout=8) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers), error.read()


class Worker(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, code, value, audio=False):
        payload = value if audio else json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'audio/wav' if audio else 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('X-Local-AI-Speech-Policy', 'paragraph80-v1')
        self.send_header('X-Local-AI-Speech-Timings', 'retained-timings')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/health':
            self.respond(200 if self.server.ready else 503, {})
        elif self.path == '/v1/runtime':
            self.respond(200, {'backend':'breeze-tts2', 'breeze_fast': False,
                               'cuda_device_name': 'test GPU', 'long_form_jobs': {'workers':1}})
        elif self.path in {'/v1/models', '/v1/voices', '/v1/audio/voices'}:
            self.respond(200, {'worker':self.server.index, 'data':[]})
        else:
            key = self.path.removeprefix('/v1/audio/jobs/').split('/')[0]
            row = self.server.jobs.get(key)
            if not row:
                self.respond(404, {'error':'unknown job'})
            elif self.path.endswith('/audio'):
                if row['state'] == 'ready':
                    self.respond(200, f"audio-{self.server.index}-{key}".encode(), audio=True)
                else:
                    self.respond(409, {'error':'not ready'})
            else:
                self.respond(200, row)

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        if self.path == '/v1/audio/jobs':
            key = uuid4().hex
            self.server.jobs[key] = {'id':key, 'state':'queued', 'completed_passages':0}
            self.respond(202, self.server.jobs[key])
        else:
            self.server.calls += 1
            if self.server.barrier:
                self.server.barrier.wait(timeout=4)
            self.respond(200, f'audio-{self.server.index}'.encode(), audio=True)

    def do_DELETE(self):
        key = self.path.removeprefix('/v1/audio/jobs/')
        row = self.server.jobs.get(key)
        if not row:
            self.respond(404, {'error':'unknown job'})
        else:
            row['state'] = 'cancelled'
            self.respond(200, row)


class GatewayTests(unittest.TestCase):
    worker_count = 2

    def setUp(self):
        self.cleanup = ExitStack()
        self.addCleanup(self.cleanup.close)
        folder = self.cleanup.enter_context(tempfile.TemporaryDirectory())
        self.workers = []
        for index in range(self.worker_count):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Worker)
            server.index, server.jobs, server.calls, server.ready, server.barrier = index, {}, 0, True, None
            self.serve(server)
            self.workers.append(server)
        self.urls = [f'http://127.0.0.1:{server.server_port}' for server in self.workers]
        self.db = Path(folder)/'routes.sqlite3'
        self.gateway = Gateway(self.urls, self.db, timeout=4, probe_timeout=.5)
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.http = server
        server.gateway = self.gateway
        self.serve(server)
        self.base = f'http://127.0.0.1:{server.server_port}'

    def serve(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.cleanup.callback(server.server_close)
        self.cleanup.callback(server.shutdown)

    def submit(self):
        status, headers, payload = call(self.base, 'POST', '/v1/audio/jobs', {'input':'Synthetic test text', 'voice':'foobar'})
        self.assertEqual(status, 202)
        return json.loads(payload), int(headers['X-Local-AI-Worker'])

    def test_concurrent_synthesis_reaches_both_workers_and_preserves_headers(self):
        barrier = threading.Barrier(2)
        for worker in self.workers:
            worker.barrier = barrier
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: call(self.base,'POST','/v1/audio/speech', {'input':'test'}), range(2)))
        self.assertEqual([worker.calls for worker in self.workers], [1,1])
        self.assertEqual({payload for _,_,payload in responses}, {b'audio-0',b'audio-1'})
        for status, headers, payload in responses:
            self.assertEqual(status,200)
            self.assertEqual(headers['X-Local-AI-Speech-Policy'], 'paragraph80-v1')
            self.assertEqual(headers['X-Local-AI-Speech-Timings'], 'retained-timings')
            self.assertEqual(int(headers['Content-Length']),len(payload))

    def test_async_ids_status_audio_and_cancel_remain_affine(self):
        first, a = self.submit()
        second, b = self.submit()
        self.assertNotEqual(a,b)
        for public, index in ((first,a),(second,b)):
            self.assertRegex(public['id'], r'^[0-9a-f]{32}$')
            remote = next(iter(self.workers[index].jobs))
            self.assertNotEqual(public['id'], remote)
            status,_,payload=call(self.base,'GET','/v1/audio/jobs/'+public['id'])
            self.assertEqual(status,200)
            self.assertEqual(json.loads(payload)['id'],public['id'])
            self.workers[index].jobs[remote]['state']='ready'
            status,headers,payload=call(self.base,'GET','/v1/audio/jobs/'+public['id']+'/audio')
            self.assertEqual(status,200)
            self.assertEqual(payload,f'audio-{index}-{remote}'.encode())
        third,index=self.submit()
        status,_,payload=call(self.base,'DELETE','/v1/audio/jobs/'+third['id'])
        self.assertEqual(status,200)
        self.assertEqual(json.loads(payload),dict(third,state='cancelled'))
        self.assertTrue(any(row['state']=='cancelled' for row in self.workers[index].jobs.values()))

    def test_routing_survives_gateway_restart_without_storing_speech(self):
        public,index=self.submit()
        restored=Gateway(self.urls,self.db,timeout=4,probe_timeout=.5)
        status,_,payload=restored.handle('GET','/v1/audio/jobs/'+public['id'])
        self.assertEqual(status,200)
        self.assertEqual(json.loads(payload)['id'],public['id'])
        self.assertNotIn(b'Synthetic test text',self.db.read_bytes())
        self.assertNotIn(b'foobar',self.db.read_bytes())

    def test_jobs_keep_their_worker_when_gpu_list_is_reordered(self):
        public, index = self.submit()
        restored = Gateway(list(reversed(self.urls)), self.db, timeout=4, probe_timeout=.5)
        status, _, payload = restored.handle('GET', '/v1/audio/jobs/' + public['id'])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['id'], public['id'])
        self.assertEqual(len(self.workers[index].jobs), 1)
        removed = Gateway([self.urls[1-index]], self.db, timeout=4, probe_timeout=.5)
        self.assertEqual(removed.handle('GET', '/v1/audio/jobs/' + public['id'])[0], 404)
        self.assertEqual(len(self.workers[1-index].jobs), 0)

    def test_legacy_routing_table_migrates_without_changing_affinity(self):
        remote = uuid4().hex
        public = uuid4().hex
        self.workers[0].jobs[remote] = {'id': remote, 'state': 'queued'}
        legacy = self.db.with_name('legacy.sqlite3')
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE routes (id TEXT PRIMARY KEY, worker INTEGER NOT NULL, '
                       'remote TEXT NOT NULL, state TEXT NOT NULL, updated REAL NOT NULL)')
            db.execute('INSERT INTO routes VALUES (?,0,?,?,?)', (public, remote, 'queued', time.time()))
        restored = Gateway(list(reversed(self.urls)), legacy, legacy_workers=self.urls)
        status, _, payload = restored.handle('GET', '/v1/audio/jobs/' + public)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['id'], public)

    def test_completed_unpolled_jobs_do_not_keep_worker_busy(self):
        first,index=self.submit()
        second,other=self.submit()
        remote=next(iter(self.workers[index].jobs))
        self.workers[index].jobs[remote]['state']='ready'
        third,chosen=self.submit()
        self.assertEqual(chosen,index)
        self.assertNotEqual(chosen,other)

    def test_worker_restart_expires_old_jobs_without_replaying_them(self):
        public,index=self.submit()
        self.workers[index].jobs.clear()
        status,_,_=call(self.base,'GET','/v1/audio/jobs/'+public['id'])
        self.assertEqual(status,404)
        self.assertEqual(sum(len(worker.jobs) for worker in self.workers),0)

    def test_degraded_service_routes_new_work_to_the_healthy_gpu(self):
        self.workers[0].ready=False
        status,_,payload=call(self.base,'GET','/health')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(payload)['status'],'degraded')
        _,index=self.submit()
        self.assertEqual(index,1)
        self.workers[1].ready=False
        status,_,_=call(self.base,'POST','/v1/audio/speech',{'input':'test'})
        self.assertEqual(status,503)
        self.assertEqual([worker.calls for worker in self.workers],[0,0])

    def test_runtime_discovery_and_bad_routes(self):
        status,_,payload=call(self.base,'GET','/v1/runtime')
        data=json.loads(payload)
        self.assertEqual(status,200)
        self.assertEqual(data['long_form_jobs']['workers'],2)
        self.assertEqual(data['gateway']['replicas'],2)
        self.assertEqual(data['cuda_device_name'],'test GPU')
        for path in ('/v1/models','/v1/audio/voices','/v1/voices'):
            self.assertEqual(call(self.base,'GET',path)[0],200)
        for path in ('/v1/audio/jobs/not-an-id','/v1/audio/jobs/'+'0'*32+'/extra','/v1/audio/jobs/'+'0'*32):
            self.assertEqual(call(self.base,'GET',path)[0],404)


class FourWorkerTests(unittest.TestCase):
    worker_count = 4
    setUp = GatewayTests.setUp
    serve = GatewayTests.serve

    # The two-worker cases above exercise degraded routing and affinity in detail.
    # This independent case checks simultaneous requests really reach all N workers.
    def test_four_concurrent_requests(self):
        barrier = threading.Barrier(4)
        for worker in self.workers:
            worker.barrier = barrier
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: call(self.base, 'POST', '/v1/audio/speech', {'input':'test'}), range(4)))
        self.assertEqual([worker.calls for worker in self.workers], [1, 1, 1, 1])
        self.assertEqual({result[2] for result in results}, {f'audio-{i}'.encode() for i in range(4)})


if __name__ == '__main__':
    unittest.main()
