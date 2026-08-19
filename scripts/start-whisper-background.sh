#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${STT_PID_FILE:-logs/whisper-server.pid}
log_file=${STT_LOG_FILE:-logs/whisper-server.log}
mkdir -p "$(dirname -- "$pid_file")" "$(dirname -- "$log_file")"

if curl -fsS --max-time 2 "http://${STT_HOST_CHECK:-127.0.0.1}:${STT_PORT:-8178}/health" >/dev/null 2>&1; then
  echo "Whisper server already healthy on port ${STT_PORT:-8178}"
  exit 0
fi
if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    echo "Whisper server already running (PID $pid)"
    exit 0
  fi
  rm -f "$pid_file"
fi
nohup "$ROOT/scripts/start-whisper.sh" >>"$log_file" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$pid_file"
for _ in $(seq 1 "${STT_START_TIMEOUT:-60}"); do
  if curl -fsS --max-time 2 "http://${STT_HOST_CHECK:-127.0.0.1}:${STT_PORT:-8178}/health" >/dev/null 2>&1; then
    echo "Whisper server ready (PID $pid, port ${STT_PORT:-8178})"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Whisper server exited; see $log_file" >&2
    rm -f "$pid_file"
    exit 1
  fi
  sleep 1
done
echo "Whisper server did not become ready within ${STT_START_TIMEOUT:-60}s; see $log_file" >&2
exit 1
