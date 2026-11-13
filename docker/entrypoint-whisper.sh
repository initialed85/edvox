#!/bin/sh
# Resident whisper-server. WHISPER_THREADS is capped below the core count on
# purpose: this node also runs the TTS daemon, and two CPU inference servers each
# grabbing every core contend for memory bandwidth and both get slower.
set -eu

MODEL=${WHISPER_MODEL:-/models/ggml-base.en.bin}

if [ ! -f "$MODEL" ]; then
  echo "whisper: missing model file $MODEL - the models init container did not finish" >&2
  exit 1
fi

# whisper-server's own parser has no -ngl: this build is CPU-only by construction
# (GGML_CUDA/GGML_METAL/GGML_OPENCL were all switched off in the image), so there is
# nothing to offload and no flag to accept it.
exec /srv/edvox/bin/whisper-server \
  -m "$MODEL" \
  --host "${WHISPER_HOST:-0.0.0.0}" \
  --port "${WHISPER_PORT:-8178}" \
  -t "${WHISPER_THREADS:-6}" \
  --language "${WHISPER_LANGUAGE:-en}" \
  --suppress-nst \
  "$@"
