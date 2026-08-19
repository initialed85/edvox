#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
text=${*:-Hello from the local voice interface.}
out=${OUTPUT:-audio/test.wav}
./bin/llama-tts \
  -m models/qwen3-tts-12hz-0.6b-base-q8_0.gguf \
  -mm models/qwen3-tts-tokenizer-12hz.gguf \
  -p "$text" \
  -o "$out"
printf "%s\\n" "$out"
