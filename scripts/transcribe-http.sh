#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ "$#" -lt 1 ]; then
  echo "usage: $0 AUDIO_FILE" >&2
  exit 2
fi
curl -fsS -X POST "${STT_URL:-http://127.0.0.1:8178}/inference" \
  -F "file=@$1" \
  -F "response_format=${STT_RESPONSE_FORMAT:-json}"
