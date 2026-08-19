#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${CHAT_PID_FILE:-logs/llama-server.pid}
if [ ! -f "$pid_file" ]; then
  echo "llama-server PID file not found"
  exit 0
fi
pid=$(cat "$pid_file")
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  for _ in $(seq 1 "${CHAT_STOP_TIMEOUT:-30}"); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
fi
rm -f "$pid_file"
echo "llama-server stopped"
