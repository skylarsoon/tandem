"""One persistent worker: subscription wake-ups and polling share the DB scan.

The shell (single-instance lock, wake endpoint, settle window, polling) runs
either engine: an operator-configured room (`serve_live`, searches from a live
JSON config) or every room's onboarding and planning (`serve.serve`)."""
from __future__ import annotations

import fcntl
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import signal
import threading
import time
from urllib.parse import urlparse

from .contracts import ContractError


class WorkerService:
    def __init__(self, config, settings, directory, room_id, *, backend=None, run=None,
                 poll_interval=5.0, settle_seconds=2.0, monotonic=time.monotonic, rooms=None, kinds=frozenset({'PLAN'})):
        if not 0 < poll_interval <= 60 or not 0 <= settle_seconds <= 10:
            raise ContractError('INVALID_SERVICE_INTERVAL')
        self.config, self.settings = config, settings
        self.directory = Path(directory).resolve()
        # One configured room, or a set of rooms (None: every room) for the multi-room engine.
        self.room_id = int(room_id) if room_id is not None else None
        self.rooms = {self.room_id} if self.room_id is not None else (set(rooms) if rooms is not None else None)
        self.kinds = frozenset(kinds)
        self.poll_interval, self.settle_seconds, self.monotonic = poll_interval, settle_seconds, monotonic
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.active = False
        self.observed = None
        if backend is None:
            from .adapters.spacetime import SpacetimeBackend
            from .live import bind_room, bind_session
            settings.require()
            settings.require_backend()
            config.validate_dates(time.time())
            self.directory.mkdir(parents=True, exist_ok=True)
            bind_session(self.directory, config, settings, 'run', time.time())
            bind_room(self.directory, room_id)
            backend = SpacetimeBackend(settings.stdb_url, settings.stdb_database,
                settings.stdb_worker_token.get_secret_value(), room_id=self.room_id, owner_name=lambda m: m['display_name'])
        self.backend = backend
        if run is None:
            from .live import run_live
            run = run_live
            test_path = os.environ.get('TANDEM_WORKER_TEST_RUNNER')
            if test_path:
                if os.environ.get('TANDEM_WORKER_TEST_MODE') != '1' or urlparse(settings.stdb_url).hostname not in {'127.0.0.1', 'localhost'}:
                    raise ContractError('TEST_RUNNER_NOT_ALLOWED')
                spec = importlib.util.spec_from_file_location('tandem_worker_test_runner', test_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                run = module.run
        self.run = run

    def scan_once(self):
        # Notifications are hints. Neither their job ID nor their arrival grants authority.
        jobs = [j for j in self.backend.pending_jobs()
                if (self.rooms is None or 'room_id' not in j or int(j['room_id']) in self.rooms or j.get('kind') == 'PROFILE')
                and ('kind' not in j or j['kind'] in self.kinds)]
        if not jobs:
            self.observed = None
            return 'NO_ELIGIBLE_WORK'
        job = jobs[-1]
        marker = tuple(str(job.get(k, '')) for k in ('id', 'input_revision', 'membership_revision', 'permission_revision'))
        now = self.monotonic()
        if self.observed is None or self.observed[0] != marker:
            self.observed = (marker, now)
        if now - self.observed[1] < self.settle_seconds:
            return 'SETTLING_INPUTS'
        self.active = True
        try:
            result = self.run(self.config, self.settings, self.directory, room_id=self.room_id)
            return result.get('result', {}).get('outcome', 'EXECUTION_FAILED')
        finally:
            self.active = False
            self.observed = None

    def stop(self):
        self.stopped.set()
        self.wake.set()

    def run_forever(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        # This only excludes another service using the same checkpoint/budget directory;
        # transactional backend claims remain the authority across all worker processes.
        with (self.directory / 'worker-service.lock').open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ContractError('WORKER_SERVICE_ALREADY_RUNNING') from None
            while not self.stopped.is_set():
                self.wake.clear()
                try:
                    outcome = self.scan_once()
                    if outcome not in {'NO_ELIGIBLE_WORK', 'SETTLING_INPUTS'}:
                        print(json.dumps({'outcome': outcome}), flush=True)
                except ContractError as exc:
                    print(json.dumps({'outcome': exc.code}), flush=True)
                except Exception:
                    print(json.dumps({'outcome': 'WORKER_SCAN_FAILED'}), flush=True)
                delay = self.poll_interval
                if self.observed is not None:
                    delay = min(delay, max(0.05, self.settle_seconds - (self.monotonic() - self.observed[1])))
                self.wake.wait(delay)


def make_http_server(service, token, host='127.0.0.1', port=8080):
    if not isinstance(token, str) or not token:
        raise ContractError('WORKER_WAKE_TOKEN_MISSING')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log incoming paths, bearer tokens or arbitrary bodies.

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(raw)
            self.close_connection = True

        def do_GET(self):
            if self.path != '/health':
                return self.reply(404, {'error': 'NOT_FOUND'})
            return self.reply(200, {'status': 'stopping' if service.stopped.is_set() else 'ok'})

        def do_POST(self):
            if self.path != '/wake':
                return self.reply(404, {'error': 'NOT_FOUND'})
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode()):
                return self.reply(403, {'error': 'NOT_AUTHORIZED'})
            if service.stopped.is_set():
                return self.reply(503, {'error': 'WORKER_STOPPING'})
            try:
                size = int(self.headers.get('Content-Length', '-1'))
                if not 0 < size <= 4096:
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                for key in ('job_id', 'room_id'):
                    value = body[key]
                    if isinstance(value, bool) or not str(value).isdigit():
                        raise ValueError()
                if service.rooms is not None and int(body['room_id']) not in service.rooms:
                    return self.reply(404, {'error': 'ROOM_NOT_CONFIGURED'})
                if body.get('kind') not in service.kinds:
                    return self.reply(202, {'accepted': False, 'reason': 'UNSUPPORTED_JOB_KIND'})
            except (ValueError, KeyError, TypeError, TimeoutError):
                return self.reply(400, {'error': 'INVALID_WAKE_REQUEST'})
            service.wake.set()
            return self.reply(202, {'accepted': True})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def run_service(service, token, *, host='127.0.0.1', port=8080, label=None):
    """Run the scan loop until stopped, with the wake endpoint when a token is set
    (polling alone otherwise: the dispatcher's wake-ups are only a speed-up)."""
    server = make_http_server(service, token, host, port) if token else None
    thread = threading.Thread(target=server.serve_forever, daemon=True) if server else None
    previous = signal.signal(signal.SIGTERM, lambda *_: service.stop())
    if thread:
        thread.start()
    print(json.dumps({'service': 'ready', **(label or {}), 'listen': f'{host}:{port}' if server else None,
                      'poll_interval': service.poll_interval}), flush=True)
    try:
        service.run_forever()
    except KeyboardInterrupt:
        service.stop()
    finally:
        service.stop()
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        signal.signal(signal.SIGTERM, previous)


def serve_live(config, settings, directory, room_id, *, host='127.0.0.1', port=8080, poll_interval=5.0, settle_seconds=2.0):
    """One operator-configured room (searches from the live JSON config)."""
    service = WorkerService(config, settings, directory, room_id, poll_interval=poll_interval, settle_seconds=settle_seconds)
    token = settings.worker_wake_token.get_secret_value() if settings.worker_wake_token else None
    if not token:
        raise ContractError('WORKER_WAKE_TOKEN_MISSING')
    run_service(service, token, host=host, port=port, label={
        'mode': 'TEST_PROVIDERS' if os.environ.get('TANDEM_WORKER_TEST_RUNNER') else 'LIVE_PROVIDERS', 'room_id': room_id})
