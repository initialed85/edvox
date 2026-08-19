#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${TTS_PID_FILE:-logs/chatterbox-server.pid}
log_file=${TTS_LOG_FILE:-logs/chatterbox-server.log}
mkdir -p "$(dirname -- "$pid_file")" "$(dirname -- "$log_file")"

if curl -fsS --max-time 2 "http://${TTS_HOST_CHECK:-127.0.0.1}:${TTS_PORT:-8190}/health" >/dev/null 2>&1; then
  echo "Chatterbox server already healthy on port ${TTS_PORT:-8190}"
  exit 0
fi
if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    echo "Chatterbox server already running (PID $pid)"
    exit 0
  fi
  rm -f "$pid_file"
fi

nohup "$ROOT/scripts/start-tts.sh" >>"$log_file" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$pid_file"
for _ in $(seq 1 "${TTS_START_TIMEOUT:-180}"); do
  if curl -fsS --max-time 2 "http://${TTS_HOST_CHECK:-127.0.0.1}:${TTS_PORT:-8190}/health" >/dev/null 2>&1; then
    echo "Chatterbox server ready (PID $pid, port ${TTS_PORT:-8190})"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Chatterbox server exited; see $log_file" >&2
    rm -f "$pid_file"
    exit 1
  fi
  sleep 1
done
echo "Chatterbox server did not become ready within ${TTS_START_TIMEOUT:-180}s; see $log_file" >&2
exit 1
