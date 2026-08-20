#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${CADDY_PID_FILE:-logs/caddy.pid}
if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then echo "PID: $pid"; else echo "PID file is stale: $pid"; fi
else
  echo "PID: not managed by this workspace"
fi
curl -kfsS "https://${CADDY_DOMAIN:-icarus.local}:${CADDY_PORT:-8443}/api/health" 2>/dev/null || true
echo
