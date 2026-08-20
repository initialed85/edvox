#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

caddy_bin=${CADDY_BIN:-/opt/homebrew/bin/caddy}
config_file=${CADDY_CONFIG_FILE:-$ROOT/config/Caddyfile}
pid_file=${CADDY_PID_FILE:-logs/caddy.pid}
log_file=${CADDY_LOG_FILE:-logs/caddy.log}

mkdir -p "$(dirname -- "$pid_file")" "$(dirname -- "$log_file")"
exec "$caddy_bin" run --config "$config_file" --adapter caddyfile
