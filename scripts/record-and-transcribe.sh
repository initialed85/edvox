#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
out=${AUDIO_OUTPUT:-audio/mic-$(date +%Y%m%d-%H%M%S).wav}
duration=${RECORD_SECONDS:-5}
mkdir -p "$(dirname "$out")"
echo "Recording for ${duration}s from MacBook Pro Microphone (device :1)..." >&2
ffmpeg -hide_banner -loglevel error \
  -f avfoundation -i "${AUDIO_DEVICE:-:1}" \
  -t "$duration" -ar 16000 -ac 1 -c:a pcm_s16le -y "$out"
echo "Transcription:" >&2
./scripts/transcribe.sh "$out"
