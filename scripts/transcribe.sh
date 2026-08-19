#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
if [ "$#" -lt 1 ]; then
  echo "usage: $0 AUDIO_FILE" >&2
  exit 2
fi
./bin/whisper-cli \
  -m "${STT_MODEL:-models/ggml-base.en.bin}" \
  -l "${STT_LANG:-en}" \
  -nt \
  -np \
  "$1"
