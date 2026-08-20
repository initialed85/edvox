#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
caddy_bin=${CADDY_BIN:-/opt/homebrew/bin/caddy}
cert=${CADDY_ROOT_CERT:-${HOME}/Library/Application Support/Caddy/pki/authorities/local/root.crt}
if [ ! -f "$cert" ]; then
  echo "Caddy root certificate not found at: $cert" >&2
  echo "Start Caddy first with ./scripts/start-caddy-background.sh" >&2
  exit 1
fi
printf '%s\n' "$cert"
if [ "${1:-}" = "--copy" ]; then
  destination=${2:-caddy-local-root.crt}
  cp "$cert" "$destination"
  echo "Copied to $destination"
fi
