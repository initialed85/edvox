#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${CADDY_PID_FILE:-logs/caddy.pid}
if [ ! -f "$pid_file" ]; then
  echo "Caddy is not managed by this workspace"
  exit 0
fi
pid=$(cat "$pid_file")
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  echo "Caddy stopped"
else
  echo "Caddy PID $pid is not running"
fi
rm -f "$pid_file"
