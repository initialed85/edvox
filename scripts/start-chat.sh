#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

server=${CHAT_SERVER_BIN:-/opt/homebrew/bin/llama-server}
if [ ! -x "$server" ]; then
  echo "llama-server not executable: $server" >&2
  exit 1
fi

# hf_repo="${CHAT_HF_REPO:-unsloth/Qwen3.6-35B-A3B-MTP-GGUF:UD-Q4_K_XL}"
hf_repo="${CHAT_HF_REPO:-HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q4_K_M}"

model_path=${CHAT_MODEL_PATH:-}
ctx_size=${CHAT_CTX_SIZE:-32768}
# spec_type=${CHAT_SPEC_TYPE:-draft-mtp}
spec_type=${CHAT_SPEC_TYPE:-none}

set -- "$server" \
  --host "${CHAT_HOST:-0.0.0.0}" \
  --port "${CHAT_PORT:-8088}" \
  --temp "${CHAT_TEMPERATURE:-0.6}" \
  --top-p "${CHAT_TOP_P:-0.95}" \
  --top-k "${CHAT_TOP_K:-20}" \
  --min-p "${CHAT_MIN_P:-0.00}" \
  --presence-penalty "${CHAT_PRESENCE_PENALTY:-0.0}" \
  --repeat-penalty "${CHAT_REPEAT_PENALTY:-1.1}" \
  --gpu-layers "${CHAT_GPU_LAYERS:-99}" \
  --flash-attn on \
  --cache-type-k "${CHAT_CACHE_TYPE_K:-q8_0}" \
  --cache-type-v "${CHAT_CACHE_TYPE_V:-q8_0}" \
  --ctx-size "$ctx_size" \
  --parallel "${CHAT_PARALLEL:-1}" \
  --cache-prompt \
  --reasoning "${CHAT_REASONING:-off}" \
  --no-ui \
  --log-verbosity "${CHAT_LOG_VERBOSITY:-4}" \
  --offline

if [ -n "$model_path" ]; then
  set -- "$@" --model "$model_path"
else
  set -- "$@" --hf-repo "$hf_repo"
fi

if [ "$spec_type" != "none" ]; then
  set -- "$@" --spec-type "$spec_type" \
    --spec-draft-n-max "${CHAT_SPEC_DRAFT_N_MAX:-2}"
fi

echo "Starting llama-server: ctx=$ctx_size parallel=${CHAT_PARALLEL:-1} spec=$spec_type" >&2
exec "$@"
