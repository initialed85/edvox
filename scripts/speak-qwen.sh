#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
text=${*:-Hello from the local voice interface.}
out=${OUTPUT:-audio/test.wav}
model=${TTS_MODEL:-models/Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf}
mmproj=${TTS_MMPROJ:-models/mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf}
lang=${TTS_LANG:-en}
frames=${TTS_FRAMES:-512}
speaker=${TTS_SPEAKER_FILE:-}
mkdir -p "$(dirname "$out")"
set -- ./bin/llama-tts \
  -m "$model" \
  -mm "$mmproj" \
  -p "$text" \
  --tts-lang "$lang" \
  -n "$frames" \
  -o "$out"
if [ -n "$speaker" ]; then
  set -- "$@" --tts-speaker-file "$speaker"
fi
"$@"
printf "%s\\n" "$out"
