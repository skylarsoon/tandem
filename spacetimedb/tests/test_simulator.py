"""The fixture simulator (scripts/simulate.py) runs end to end against the module,
including a rehearsed worker crash followed by a takeover."""

import json
import hashlib
import secrets
import tempfile
import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import STDB_DB, STDB_URL, STDB_SERVER, SPACETIME

SIMULATE = Path(__file__).resolve().parents[1] / "scripts" / "simulate.py"


def simulate(*args: str, token: str | None = None) -> str:
    env = {**os.environ, "STDB_URL": STDB_URL, "STDB_DB": STDB_DB}
    if token:
        env["STDB_WORKER_TOKEN"] = token
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as invitation_file:
        if args[0] == "seed":
            people = int(args[args.index("--people") + 1])
            invitations = [secrets.token_urlsafe(32) for _ in range(people)]
            for invitation in invitations:
                subprocess.run([SPACETIME, "call", "--server", STDB_SERVER, "--no-config", "-y", STDB_DB,
                    "create_app_invitation", json.dumps(hashlib.sha256(invitation.encode()).hexdigest()),
                    json.dumps("simulator-test"), "600"], check=True, capture_output=True)
            json.dump(invitations, invitation_file); invitation_file.flush()
            args = (*args, "--invitations-file", invitation_file.name)
        result = subprocess.run([sys.executable, str(SIMULATE), *args], env=env, capture_output=True, text=True,
                                timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_seed_then_negotiate_publishes_a_plan(client, worker):
    out = simulate("seed", "--people", "4")
    room_id = int(re.search(r"room_id=(\d+)", out).group(1))
    out = simulate("negotiate", "--room", str(room_id), "--pace", "0.05", token=worker.token)
    assert re.search(r"published proposal \d+ v1", out), out


def test_crash_rehearsal_then_takeover(client, worker):
    room_id = int(re.search(r"room_id=(\d+)", simulate("seed", "--people", "3")).group(1))
    crashed = simulate("negotiate", "--room", str(room_id), "--pace", "0.05", "--crash-after", "1",
                       token=worker.token)
    assert "simulated crash" in crashed and "(attempt 1)" in crashed
    resumed = simulate("negotiate", "--room", str(room_id), "--pace", "0.05", token=worker.token)
    assert "(attempt 2)" in resumed, "the job must come back after the lease expires"
    assert "published proposal" in resumed
