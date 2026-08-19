#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${CHAT_PID_FILE:-logs/llama-server.pid}
if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    echo "PID: $pid"
    ps -p "$pid" -o pid,etime,%cpu,%mem,rss,command=
  else
    echo "stale PID file: $pid_file"
  fi
else
  echo "PID: not managed by this workspace"
fi
curl -fsS --max-time 3 "http://${CHAT_HOST_CHECK:-127.0.0.1}:${CHAT_PORT:-8088}/health" && echo
