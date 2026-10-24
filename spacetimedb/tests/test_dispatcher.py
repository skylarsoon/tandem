"""Dispatcher wake-ups (W05 backend side): a stub worker endpoint receives one
POST per claimable job, both for jobs pending at startup and for new ones."""

import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import STDB_DB, STDB_URL, Room, register_worker

DISPATCHER_DIR = Path(__file__).resolve().parents[2] / "dispatcher"

pytestmark = pytest.mark.skipif(
    not (DISPATCHER_DIR / "node_modules").exists() or shutil.which("npx") is None,
    reason="dispatcher dependencies not installed (cd dispatcher && npm install)",
)


class WakeRecorder(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802 (http.server naming)
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        WakeRecorder.received.append(json.loads(body))
        self.send_response(202)
        self.end_headers()

    def log_message(self, *args):
        pass


def wait_for(predicate, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def test_dispatcher_wakes_worker_for_pending_jobs(room: Room, client, worker):
    WakeRecorder.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), WakeRecorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    dispatcher = client.mint_identity()
    register_worker(dispatcher, "pytest-dispatcher")

    room.set_constraint("alice", "flight_budget_total", 50000)
    first_job = room.plan_job(worker)

    env = {**os.environ, "STDB_URL": STDB_URL, "STDB_DB": STDB_DB, "STDB_TOKEN": dispatcher.token,
           "WORKER_WAKE_URL": f"http://127.0.0.1:{server.server_port}/wake"}
    proc = subprocess.Popen(["npx", "tsx", "src/index.ts"], cwd=DISPATCHER_DIR, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        woken = lambda job_id: any(w["job_id"] == job_id for w in WakeRecorder.received)  # noqa: E731
        assert wait_for(lambda: woken(first_job)), "no wake-up for the job pending at startup"

        room.set_constraint("bob", "flight_budget_total", 65000)
        second_job = room.plan_job(worker)
        assert wait_for(lambda: woken(second_job)), "no wake-up for a newly created job"
        wake = next(w for w in WakeRecorder.received if w["job_id"] == second_job)
        assert wake == {"job_id": second_job, "room_id": room.room_id, "kind": "PLAN"}
    finally:
        proc.terminate()
        output = proc.communicate(timeout=10)[0]
        server.shutdown()
        if not WakeRecorder.received:
            print(output)
