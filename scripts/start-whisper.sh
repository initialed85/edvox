#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
exec ./bin/whisper-server \
  --host "${STT_HOST:-127.0.0.1}" \
  --port "${STT_PORT:-8178}" \
  --model "${STT_MODEL:-models/ggml-base.en.bin}" \
  --convert
