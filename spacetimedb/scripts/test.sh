#!/usr/bin/env bash
# Runs the backend contract tests against a throwaway in-memory SpacetimeDB.
# Usage: spacetimedb/scripts/test.sh [pytest args...]
set -euo pipefail

SPACETIME="${SPACETIME_BIN:-$HOME/.local/bin/spacetime}"
PORT="${STDB_TEST_PORT:-3100}"
DB="tandem-test-$(date +%s)"  # unique per run: database names stay owned by whoever first published them
MODULE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if curl -s "http://127.0.0.1:$PORT/v1/ping" >/dev/null 2>&1; then
  echo "Port $PORT is already in use; set STDB_TEST_PORT to another port." >&2
  exit 1
fi

"$SPACETIME" start --in-memory --listen-addr "127.0.0.1:$PORT" --non-interactive >/dev/null 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  curl -s "http://127.0.0.1:$PORT/v1/ping" >/dev/null 2>&1 && break
  sleep 0.2
done

# Reuse whichever CLI server nickname points at this port; add one if none does.
SERVER="$("$SPACETIME" server list 2>/dev/null | awk -v host="127.0.0.1:$PORT" '$0 ~ host {print $NF}' | head -1)"
if [ -z "$SERVER" ]; then
  SERVER="tandem-test-$PORT"
  "$SPACETIME" server add "$SERVER" --url "http://127.0.0.1:$PORT" --no-fingerprint >/dev/null 2>&1
fi
"$SPACETIME" publish --server "$SERVER" --module-path "$MODULE_DIR" --delete-data=always --yes "$DB" >/dev/null

cd "$MODULE_DIR/tests"
STDB_URL="http://127.0.0.1:$PORT" STDB_DB="$DB" STDB_SERVER="$SERVER" SPACETIME_BIN="$SPACETIME" \
  uv run --no-project --python 3.11 --with pytest pytest -q "$@"
