#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 1 || ! "$1" =~ ^[0-9]+$ ]]; then
  echo "Usage: bash scripts/start-worker.sh ROOM_ID" >&2
  exit 2
fi

cd "$repo_root/worker"
exec .venv/bin/tandem-worker serve \
  --config live.json \
  --env-file ../.env.local \
  --state-dir ./live-state/browser-demo \
  --room "$1" \
  --port 8080
