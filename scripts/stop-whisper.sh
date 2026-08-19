#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${STT_PID_FILE:-logs/whisper-server.pid}
if [ ! -f "$pid_file" ]; then
  echo "Whisper server is not managed by this workspace"
  exit 0
fi
pid=$(cat "$pid_file")
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  echo "Whisper server stopped"
else
  echo "Whisper server PID $pid is not running"
fi
rm -f "$pid_file"
