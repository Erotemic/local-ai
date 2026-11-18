#!/usr/bin/env python3
"""One Breeze endpoint for independent GPU replicas, including async job affinity.

The SQLite routing table contains only opaque job IDs, worker identities, states,
and timestamps. Synthesis inputs, voice references, and audio are not persisted.
A submitted inference is never silently replayed on another worker.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

JOB = re.compile(r'^/v1/audio/jobs/([0-9a-f]{32})(/audio)?$')
TERMINAL = {'ready', 'error', 'cancelled', 'expired'}
DISCOVERY = {'/v1/models', '/v1/audio/voices', '/v1/voices'}


class Gateway:
    def __init__(self, workers, database, *, timeout=1800, probe_timeout=3, gpu_ids=None, legacy_workers=None):
        self.workers = [url.rstrip('/') for url in workers]
        if not self.workers or len(set(self.workers)) != len(self.workers):
            raise ValueError('distinct Breeze worker URLs are required')
        if timeout <= 0 or probe_timeout <= 0:
            raise ValueError('gateway timeouts must be positive')
        self.gpu_ids = list(gpu_ids) if gpu_ids is not None else list(range(len(workers)))
        if len(self.gpu_ids) != len(workers):
            raise ValueError('GPU IDs must correspond to the configured workers')
        self.timeout, self.probe_timeout = timeout, probe_timeout
        self.database = str(database)
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.inflight = [0] * len(workers)
        self.cursor = 0
        with self.db() as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS routes '
                       '(id TEXT PRIMARY KEY, worker INTEGER NOT NULL, remote TEXT NOT NULL, '
                       'state TEXT NOT NULL, updated REAL NOT NULL, endpoint TEXT)')
            columns = {row[1] for row in db.execute('PRAGMA table_info(routes)')}
            if 'endpoint' not in columns:
                db.execute('ALTER TABLE routes ADD COLUMN endpoint TEXT')
            # The former fixed two-worker recipe had these stable DNS names.
            # Persist endpoint identities so reordering/removing GPUs can never
            # send an existing job to a different worker.
            for index, endpoint in enumerate(legacy_workers or ['http://breeze-worker-0:8080', 'http://breeze-worker-1:8080']):
                db.execute('UPDATE routes SET endpoint=? WHERE worker=? AND endpoint IS NULL', (endpoint, index))

    def db(self):
        return closing(sqlite3.connect(self.database, timeout=10))

    def request(self, worker, method, path, body=None, *, probe=False):
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        req = Request(self.workers[worker] + path, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.probe_timeout if probe else self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            return error.code, dict(error.headers), error.read()

    @staticmethod
    def json_response(status, value):
        return status, {'Content-Type': 'application/json'}, json.dumps(value).encode()

    def healthy(self):
        def probe(index):
            try:
                status, _, _ = self.request(index, 'GET', '/health', probe=True)
                return index, status == 200
            except (OSError, URLError):
                return index, False
        with ThreadPoolExecutor(max_workers=len(self.workers)) as pool:
            return dict(pool.map(probe, range(len(self.workers))))

    def record_state(self, public_id, state):
        with self.db() as db, db:
            db.execute('UPDATE routes SET state=?, updated=? WHERE id=?', (state, time.time(), public_id))

    def reconcile(self):
        # Poll only outstanding jobs. Clients need not stay connected to release
        # finished jobs from the load estimate, and concurrent submissions share
        # one bounded reconciliation pass.
        if not self.refresh_lock.acquire(blocking=False):
            return
        try:
            with self.db() as db:
                rows = db.execute("SELECT id, endpoint, remote FROM routes WHERE state NOT IN ('ready','error','cancelled','expired')").fetchall()
            def inspect(row):
                public_id, endpoint, remote = row
                if endpoint not in self.workers:
                    self.record_state(public_id, 'expired')
                    return
                worker = self.workers.index(endpoint)
                try:
                    status, _, payload = self.request(worker, 'GET', '/v1/audio/jobs/' + remote, probe=True)
                    if status == 404:
                        self.record_state(public_id, 'expired')
                    elif status == 200:
                        state = json.loads(payload).get('state')
                        if state in TERMINAL:
                            self.record_state(public_id, state)
                except (OSError, URLError, ValueError, IndexError):
                    pass  # An unavailable worker does not make its jobs replayable.
            if rows:
                with ThreadPoolExecutor(max_workers=min(8, len(rows))) as pool:
                    list(pool.map(inspect, rows))
        finally:
            self.refresh_lock.release()

    def choose(self, ready):
        self.reconcile()
        with self.lock:
            with self.db() as db:
                counts = dict(db.execute("SELECT endpoint, count(*) FROM routes WHERE state NOT IN ('ready','error','cancelled','expired') GROUP BY endpoint"))
            available = [i for i in range(len(self.workers)) if ready.get(i)]
            if not available:
                raise ConnectionError('No Breeze worker is ready')
            worker = min(available, key=lambda i: (counts.get(self.workers[i], 0) + self.inflight[i], (i - self.cursor) % len(self.workers)))
            self.inflight[worker] += 1
            self.cursor = (worker + 1) % len(self.workers)
            return worker

    def job(self, method, public_id, audio):
        if method not in ('GET', 'DELETE') or (audio and method != 'GET'):
            return self.json_response(405, {'error': 'method not allowed'})
        with self.db() as db:
            route = db.execute('SELECT endpoint, remote FROM routes WHERE id=?', (public_id,)).fetchone()
        if not route:
            return self.json_response(404, {'error': 'unknown or expired speech job'})
        endpoint, remote = route
        if endpoint not in self.workers:
            self.record_state(public_id, 'expired')
            return self.json_response(404, {'error': 'speech job worker is no longer configured'})
        worker = self.workers.index(endpoint)
        status, headers, payload = self.request(worker, method, '/v1/audio/jobs/' + remote + ('/audio' if audio else ''))
        if status == 404:
            self.record_state(public_id, 'expired')
        if status == 200 and not audio:
            value = json.loads(payload)
            value['id'] = public_id
            self.record_state(public_id, value.get('state', 'queued'))
            payload = json.dumps(value).encode()
        elif status == 200 and audio:
            self.record_state(public_id, 'ready')
        return status, headers, payload

    def handle(self, method, path, body=None):
        if match := JOB.fullmatch(path):
            return self.job(method, match[1], bool(match[2]))
        if not ((method == 'GET' and path in DISCOVERY | {'/health', '/v1/runtime'}) or
                (method == 'POST' and path in {'/v1/audio/speech', '/v1/audio/jobs'})):
            return self.json_response(404, {'error': 'not found'})
        ready = self.healthy()
        if path == '/health':
            return self.json_response(200 if any(ready.values()) else 503,
                {'status': 'ok' if all(ready.values()) else 'degraded', 'backend': 'breeze-tts2',
                 'workers': [{'index': index, 'gpu': self.gpu_ids[index], 'ready': health} for index, health in ready.items()]})
        if path == '/v1/runtime':
            workers, primary = [], None
            for index, health in ready.items():
                info = None
                if health:
                    try:
                        status, _, payload = self.request(index, 'GET', path, probe=True)
                        if status == 200:
                            info = json.loads(payload)
                            if primary is None:
                                primary = dict(info)
                    except (OSError, URLError, ValueError):
                        pass
                workers.append({'index': index, 'gpu': self.gpu_ids[index], 'ready': info is not None, 'runtime': info})
            if primary is None:
                return self.json_response(503, {'error': 'No Breeze worker is ready'})
            primary['gateway'] = {'strategy': 'least-outstanding', 'replicas': len(workers), 'gpus': self.gpu_ids, 'workers': workers}
            primary['long_form_jobs'] = {**primary.get('long_form_jobs', {}), 'workers': sum(w['ready'] for w in workers)}
            return self.json_response(200, primary)
        if not any(ready.values()):
            return self.json_response(503, {'error': 'No Breeze worker is ready'})
        worker = self.choose(ready)
        try:
            status, headers, payload = self.request(worker, method, path, body)
            if path == '/v1/audio/jobs' and status == 202:
                value = json.loads(payload)
                remote = value.get('id', '')
                if not isinstance(remote, str) or not re.fullmatch('[0-9a-f]{32}', remote):
                    raise ValueError('Breeze worker returned an invalid job ID')
                public_id = uuid4().hex  # Preserve the reader's 32-hex-ID contract.
                with self.db() as db, db:
                    db.execute('DELETE FROM routes WHERE updated < ?', (time.time() - 86400,))
                    db.execute('INSERT INTO routes (id,worker,remote,state,updated,endpoint) VALUES (?,?,?,?,?,?)',
                        (public_id, worker, remote, value.get('state', 'queued'), time.time(), self.workers[worker]))
                value['id'] = public_id
                payload = json.dumps(value).encode()
            headers['X-Local-AI-Worker'] = str(worker)
            return status, headers, payload
        finally:
            with self.lock:
                self.inflight[worker] -= 1


class Handler(BaseHTTPRequestHandler):
    server_version = 'local-ai-breeze-gateway/1'

    def log_message(self, fmt, *args):
        # Paths and status only. Never log request bodies or reference material.
        print(fmt % args, flush=True)

    def dispatch(self):
        body = None
        if self.command == 'POST':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 262144:
                    raise ValueError('request must contain 1–262144 bytes')
                body = self.rfile.read(length)
            except ValueError:
                self.respond(*Gateway.json_response(400, {'error': 'request must contain 1–262144 bytes'}))
                return
        try:
            result = self.server.gateway.handle(self.command, self.path, body)
        except (ValueError, TypeError):
            result = Gateway.json_response(502, {'error': 'invalid Breeze worker response; inference is not automatically replayed'})
        except (OSError, URLError, sqlite3.Error):
            result = Gateway.json_response(502, {'error': 'Breeze worker unavailable; submitted inference is not automatically replayed'})
        self.respond(*result)

    def respond(self, status, headers, payload):
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() in {'content-type', 'cache-control', 'retry-after'} or key.lower().startswith('x-local-ai-'):
                self.send_header(key, value)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Inference may still have completed; do not duplicate it.

    do_GET = do_POST = do_DELETE = dispatch


def main():
    workers = [value.strip() for value in os.environ.get('BREEZE_WORKERS', '').split(',') if value.strip()]
    gpu_ids = os.environ.get('BREEZE_GPU_IDS')
    gateway = Gateway(workers, os.environ.get('BREEZE_GATEWAY_DB', '/state/routes.sqlite3'),
                      timeout=float(os.environ.get('BREEZE_GATEWAY_TIMEOUT', '1800')),
                      gpu_ids=gpu_ids.split(',') if gpu_ids else None)
    server = ThreadingHTTPServer((os.environ.get('BREEZE_GATEWAY_HOST', '127.0.0.1'),
                                  int(os.environ.get('BREEZE_GATEWAY_PORT', '8080'))), Handler)
    server.gateway = gateway
    server.serve_forever()


if __name__ == '__main__':
    main()
