#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
base=https://huggingface.co/cstr
if [ ! -f models/qwen3-tts-12hz-0.6b-base-q8_0.gguf ]; then
  curl -fL --retry 3 --progress-bar -o models/qwen3-tts-12hz-0.6b-base-q8_0.gguf \
    "$base/qwen3-tts-0.6b-base-GGUF/resolve/main/qwen3-tts-12hz-0.6b-base-q8_0.gguf"
fi
if [ ! -f models/qwen3-tts-tokenizer-12hz.gguf ]; then
  curl -fL --retry 3 --progress-bar -o models/qwen3-tts-tokenizer-12hz.gguf \
    "$base/qwen3-tts-tokenizer-12hz-GGUF/resolve/main/qwen3-tts-tokenizer-12hz.gguf"
fi
