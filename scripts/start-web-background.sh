#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${WEB_PID_FILE:-logs/web-server.pid}
log_file=${WEB_LOG_FILE:-logs/web-server.log}
mkdir -p "$(dirname -- "$pid_file")" "$(dirname -- "$log_file")"

if curl -fsS --max-time 2 "http://${WEB_HOST_CHECK:-127.0.0.1}:${WEB_PORT:-8200}/api/session" >/dev/null 2>&1; then
  echo "Web frontend already healthy on port ${WEB_PORT:-8200}"
  exit 0
fi
if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    echo "Web frontend already running (PID $pid)"
    exit 0
  fi
  rm -f "$pid_file"
fi
nohup "$ROOT/scripts/start-web.sh" >>"$log_file" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$pid_file"
for _ in $(seq 1 "${WEB_START_TIMEOUT:-20}"); do
  if curl -fsS --max-time 2 "http://${WEB_HOST_CHECK:-127.0.0.1}:${WEB_PORT:-8200}/api/session" >/dev/null 2>&1; then
    echo "Web frontend ready (PID $pid, port ${WEB_PORT:-8200})"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Web frontend exited; see $log_file" >&2
    rm -f "$pid_file"
    exit 1
  fi
  sleep 1
done
echo "Web frontend did not become ready within ${WEB_START_TIMEOUT:-20}s; see $log_file" >&2
exit 1
