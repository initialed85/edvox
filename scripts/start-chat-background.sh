#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${CHAT_PID_FILE:-logs/llama-server.pid}
log_file=${CHAT_LOG_FILE:-logs/llama-server.log}
mkdir -p "$(dirname -- "$pid_file")" "$(dirname -- "$log_file")"

# Do not start a second server if the configured endpoint is already healthy.
# This also avoids reporting success for an old process after a bind failure.
if curl -fsS --max-time 2 "http://${CHAT_HOST_CHECK:-127.0.0.1}:${CHAT_PORT:-8088}/health" >/dev/null 2>&1; then
  echo "llama-server already healthy on port ${CHAT_PORT:-8088}; not managed by this invocation"
  exit 0
fi

if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    echo "llama-server already running (PID $pid)"
    exit 0
  fi
  rm -f "$pid_file"
fi

nohup "$ROOT/scripts/start-chat.sh" >>"$log_file" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$pid_file"

for _ in $(seq 1 "${CHAT_START_TIMEOUT:-120}"); do
  if curl -fsS --max-time 2 "http://${CHAT_HOST_CHECK:-127.0.0.1}:${CHAT_PORT:-8088}/health" >/dev/null 2>&1; then
    echo "llama-server ready (PID $pid, port ${CHAT_PORT:-8088})"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "llama-server exited; see $log_file" >&2
    rm -f "$pid_file"
    exit 1
  fi
  sleep 1
done

echo "llama-server did not become ready within ${CHAT_START_TIMEOUT:-120}s; see $log_file" >&2
exit 1
