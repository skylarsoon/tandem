#!/usr/bin/env bash
# Real subscriptions -> TypeScript dispatcher -> Python wake endpoint, with fake providers.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPACETIME="${SPACETIME_BIN:-$(command -v spacetime || true)}"
if [ -z "$SPACETIME" ]; then echo 'Set SPACETIME_BIN to the SpacetimeDB 2.13 CLI.' >&2; exit 1; fi
if [ ! -x "$ROOT/dispatcher/node_modules/.bin/tsx" ]; then echo 'Run npm ci in dispatcher/ first.' >&2; exit 1; fi
PORT="${STDB_TEST_PORT:-3301}"
WEB_PORT="${TANDEM_WEB_PORT:-3401}"
WORKER_PORT="${WORKER_TEST_PORT:-3501}"
DB="tandem-web-test-$(date +%s)"
URL="http://127.0.0.1:$PORT"
STATE="$(mktemp -d /tmp/tandem-web-test.XXXXXX)"
SERVER_PID=''; WEB_PID=''; WORKER_PID=''; DISPATCHER_PID=''
cleanup() { for pid in "$DISPATCHER_PID" "$WEB_PID" "$WORKER_PID" "$SERVER_PID"; do if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi; done; }
trap cleanup EXIT
for p in "$PORT" "$WEB_PORT" "$WORKER_PORT"; do
 if curl -s "http://127.0.0.1:$p" >/dev/null 2>&1; then echo "Port $p is already in use." >&2; exit 1; fi
done
"$SPACETIME" --root-dir "$STATE/stdb" start --in-memory --listen-addr "127.0.0.1:$PORT" --non-interactive >"$STATE/database.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 100); do curl -sf "$URL/v1/ping" >/dev/null && break; sleep .2; done
curl -sf "$URL/v1/ping" >/dev/null
"$SPACETIME" --root-dir "$STATE/stdb" publish --server "$URL" --module-path "$ROOT/spacetimedb" --no-config --yes "$DB" >"$STATE/publish.log" 2>&1
IDENTITY_JSON="$(curl -sf -X POST "$URL/v1/identity")"
WORKER_ID="$(printf '%s' "$IDENTITY_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin)["identity"])')"
WORKER_TOKEN="$(printf '%s' "$IDENTITY_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')"
"$SPACETIME" --root-dir "$STATE/stdb" call --server "$URL" --no-config -y "$DB" add_worker "{\"__identity__\":\"0x$WORKER_ID\"}" '"browser-test-worker"' >"$STATE/worker.log" 2>&1
export TANDEM_STDB_URL="$URL" TANDEM_STDB_DB="$DB" TANDEM_STDB_WORKER_TOKEN="$WORKER_TOKEN"
export STDB_URL="$URL" STDB_DB="$DB" STDB_WORKER_TOKEN="$WORKER_TOKEN"
export ANTHROPIC_API_KEY=test-key CLAUDE_MODEL=claude-test APIFY_TOKEN=test-key WORKER_WAKE_TOKEN=browser-test-wake-secret
export TANDEM_WORKER_TEST_MODE=1 TANDEM_WORKER_TEST_RUNNER="$ROOT/web/e2e/service_runner.py"
"$ROOT/worker/.venv/bin/python" - "$ROOT" "$STATE" <<'PYTEST'
import json, os, sys
from pathlib import Path
root, state = map(Path, sys.argv[1:])
config = json.loads((root / 'worker/live.example.json').read_text())
brief, search = config['briefs'][0], config['searches'][0]
config['briefs'] = [dict(brief, owner=owner) for owner in ('p1', 'p2')]
config['searches'] = [dict(search, owner=owner) for owner in ('p1', 'p2')]
config['limits'].update(model_calls=20, actor_runs=3)
(state/'live.json').write_text(json.dumps(config))
keys = ('STDB_URL','STDB_DB','STDB_WORKER_TOKEN','ANTHROPIC_API_KEY','CLAUDE_MODEL','APIFY_TOKEN','WORKER_WAKE_TOKEN')
path = state/'worker.env'
path.write_text(''.join(f'{k}={os.environ[k]}\n' for k in keys))
path.chmod(0o600)
PYTEST
PYTHONPATH="$ROOT/worker/src" "$ROOT/worker/.venv/bin/python" -m tandem_worker.cli serve --config "$STATE/live.json" --env-file "$STATE/worker.env" --state-dir "$STATE/service" --room 1 --port "$WORKER_PORT" --poll-interval 30 --settle-seconds 1 >"$STATE/service.log" 2>&1 &
WORKER_PID=$!
for _ in $(seq 1 100); do curl -sf "http://127.0.0.1:$WORKER_PORT/health" >/dev/null && break; sleep .2; done
curl -sf "http://127.0.0.1:$WORKER_PORT/health" >/dev/null
STDB_TOKEN="$WORKER_TOKEN" WORKER_WAKE_URL="http://127.0.0.1:$WORKER_PORT/wake" "$ROOT/dispatcher/node_modules/.bin/tsx" "$ROOT/dispatcher/src/index.ts" >"$STATE/dispatcher.log" 2>&1 &
DISPATCHER_PID=$!
cd "$ROOT/web"
TANDEM_NEXT_DIST_DIR=.next-integration NEXT_PUBLIC_STDB_URL="$URL" NEXT_PUBLIC_STDB_DB="$DB" ./node_modules/.bin/next dev --hostname 127.0.0.1 --port "$WEB_PORT" >"$STATE/web.log" 2>&1 &
WEB_PID=$!
for _ in $(seq 1 100); do curl -sf "http://127.0.0.1:$WEB_PORT" >/dev/null && break; sleep .2; done
curl -sf "http://127.0.0.1:$WEB_PORT" >/dev/null
TANDEM_WEB_URL="http://127.0.0.1:$WEB_PORT" ./node_modules/.bin/playwright test "$@"
grep -q 'woke worker for job .* (PLAN): HTTP 202' "$STATE/dispatcher.log"
