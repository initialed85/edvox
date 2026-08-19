#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

python_bin="$ROOT/.venv-chatterbox/bin/python"
if [ ! -x "$python_bin" ]; then
  echo "Chatterbox Python environment not found: $python_bin" >&2
  exit 1
fi

exec "$python_bin" "$ROOT/src/chatterbox_server.py" "$@"
