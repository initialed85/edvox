#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${TTS_PID_FILE:-logs/chatterbox-server.pid}
if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    echo "PID: $pid"
  else
    echo "PID file is stale: $pid"
  fi
else
  echo "PID: not managed by this workspace"
fi
curl -fsS "http://${TTS_HOST_CHECK:-127.0.0.1}:${TTS_PORT:-8190}/health" 2>/dev/null || true
echo
