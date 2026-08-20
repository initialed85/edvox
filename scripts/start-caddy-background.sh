#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
pid_file=${CADDY_PID_FILE:-logs/caddy.pid}
log_file=${CADDY_LOG_FILE:-logs/caddy.log}
config_file=${CADDY_CONFIG_FILE:-$ROOT/config/Caddyfile}
caddy_bin=${CADDY_BIN:-/opt/homebrew/bin/caddy}
mkdir -p "$(dirname -- "$pid_file")" "$(dirname -- "$log_file")"

if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    echo "Caddy already running (PID $pid)"
    exit 0
  fi
  rm -f "$pid_file"
fi

nohup "$caddy_bin" run --config "$config_file" --adapter caddyfile >>"$log_file" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$pid_file"

for _ in $(seq 1 "${CADDY_START_TIMEOUT:-20}"); do
  if curl -kfsS --max-time 2 "https://${CADDY_DOMAIN:-icarus.local}:${CADDY_PORT:-8443}/api/health" >/dev/null 2>&1; then
    echo "Caddy ready (PID $pid, https://${CADDY_DOMAIN:-icarus.local}:${CADDY_PORT:-8443})"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Caddy exited; see $log_file" >&2
    rm -f "$pid_file"
    exit 1
  fi
  sleep 1
done

echo "Caddy did not become ready within ${CADDY_START_TIMEOUT:-20}s; see $log_file" >&2
exit 1
