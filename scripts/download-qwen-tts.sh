#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
repo=https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF/resolve/main
model=models/Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf
mmproj=models/mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf
if [ ! -f "$model" ]; then
  curl -fL --retry 3 --progress-bar -o "$model" "$repo/Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf"
fi
if [ ! -f "$mmproj" ]; then
  curl -fL --retry 3 --progress-bar -o "$mmproj" "$repo/mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf"
fi
