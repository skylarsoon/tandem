#!/usr/bin/env bash
# Runs the Python worker against a real, throwaway in-memory SpacetimeDB:
# starts a server, publishes the module, registers a worker identity, then runs
# worker/tests/backend (contract invariants + end-to-end planning runs).
# Usage: scripts/backend-integration.sh [pytest args...]
# Needs: the spacetime CLI, and worker/.venv set up as in worker/README.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPACETIME="${SPACETIME_BIN:-$HOME/.local/bin/spacetime}"
PORT="${STDB_TEST_PORT:-3200}"
DB="tandem-integration"
URL="http://127.0.0.1:$PORT"

if curl -s "$URL/v1/ping" >/dev/null 2>&1; then
  echo "Port $PORT is already in use; set STDB_TEST_PORT to another port." >&2
  exit 1
fi

"$SPACETIME" start --in-memory --listen-addr "127.0.0.1:$PORT" --non-interactive >/dev/null 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do curl -s "$URL/v1/ping" >/dev/null 2>&1 && break; sleep 0.2; done

SERVER="$("$SPACETIME" server list 2>/dev/null | awk -v host="127.0.0.1:$PORT" '$0 ~ host {print $NF}' | head -1)"
if [ -z "$SERVER" ]; then
  SERVER="tandem-test-$PORT"
  "$SPACETIME" server add "$SERVER" --url "$URL" --no-fingerprint >/dev/null 2>&1
fi
"$SPACETIME" publish --server "$SERVER" --module-path "$ROOT/spacetimedb" --delete-data=always --yes "$DB" >/dev/null

IDENTITY_JSON="$(curl -s -X POST "$URL/v1/identity")"
WORKER_ID="$(printf '%s' "$IDENTITY_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["identity"])')"
WORKER_TOKEN="$(printf '%s' "$IDENTITY_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["token"])')"
"$SPACETIME" call --server "$SERVER" --no-config -y "$DB" add_worker "{\"__identity__\":\"0x$WORKER_ID\"}" '"integration-worker"' >/dev/null 2>&1

cd "$ROOT/worker"
TANDEM_STDB_URL="$URL" TANDEM_STDB_DB="$DB" TANDEM_STDB_WORKER_TOKEN="$WORKER_TOKEN" \
  .venv/bin/python -m pytest tests/backend -q -p no:cacheprovider "$@"
