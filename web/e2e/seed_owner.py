"""Seed legacy provider-fixture inputs as the browser's owner, never in production.

The subscription harness still exercises the existing flight-only worker. The
new UI no longer exposes those legacy inputs as permanent forms.
"""
import json
import os
import sys
from urllib.parse import urlparse

from tandem_worker.adapters.spacetime import StdbHttp

url = os.environ["TANDEM_STDB_URL"]
if urlparse(url).hostname != "127.0.0.1":
    raise SystemExit("Only a disposable local database is supported")
data = json.load(sys.stdin)
http = StdbHttp(url, os.environ["TANDEM_STDB_DB"])
http.call(data["token"], "confirm_participation_preferences", speak_up_for_me=True, ask_me_first=True,
          give_me_time=True, reminders_opted_in=False)
http.call(data["token"], "set_constraint", room_id=int(data["room"]), key="flight_budget_total",
          value_json=json.dumps(50100 if data["name"] == "p1" else 50000), unit="USD_CENTS", hardness="HARD",
          authorized_range_json=None, private_rationale=None)
