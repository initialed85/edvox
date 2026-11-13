#!/bin/sh
# Assemble the daemon's llama.cpp arguments from the environment so the Kubernetes
# manifest can retune it without rebuilding an image.
#
# TTS_GPU_LAYERS/TTS_DEVICE are the knobs for the day a GPU lands on this node:
# 0 + "none" is the CPU-only configuration measured on the 5600G; 99 + "auto" is
# what an RTX 2080 in august-desktop would use.
set -eu

MODEL=${TTS_MODEL:-/models/Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf}
MMPROJ=${TTS_MMPROJ:-/models/mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf}

for path in "$MODEL" "$MMPROJ"; do
  if [ ! -f "$path" ]; then
    echo "tts: missing model file $path - the models init container did not finish" >&2
    exit 1
  fi
done

# Optional KV-cache quantization. The sentiment-analyzer deployment runs llama-server
# with -ctk q8_0 -ctv q4_0 on a GPU; on a CPU the win is smaller but real, because KV
# traffic is memory bandwidth and this box has a fixed amount of it.
EXTRA=""
if [ -n "${TTS_CACHE_K:-}" ]; then EXTRA="$EXTRA -ctk ${TTS_CACHE_K}"; fi
if [ -n "${TTS_CACHE_V:-}" ]; then EXTRA="$EXTRA -ctv ${TTS_CACHE_V}"; fi

exec /srv/edvox/bin/ttsd \
  -m "$MODEL" \
  --mmproj "$MMPROJ" \
  -t "${TTS_THREADS:-8}" \
  --ctx-size "${TTS_CTX_SIZE:-4096}" \
  -ngl "${TTS_GPU_LAYERS:-0}" \
  --device "${TTS_DEVICE:-none}" \
  --top-k "${TTS_TOP_K:-40}" \
  --top-p "${TTS_TOP_P:-0.9}" \
  --temp "${TTS_TEMP:-0.8}" \
  $EXTRA \
  "$@"
