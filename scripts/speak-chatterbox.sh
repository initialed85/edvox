#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
text=${*:-Hello from Chatterbox Nano.}
out=${OUTPUT:-audio/chatterbox-nano.wav}
model=${CHATTERBOX_MODEL:-nano}
model_dir=${CHATTERBOX_MODEL_DIR:-models/chatterbox-$model}
device=${CHATTERBOX_DEVICE:-mps}
reference=${TTS_SPEAKER_FILE:-}

set -- .venv-chatterbox/bin/python src/chatterbox_tts.py "$text" \
  --output "$out" --model "$model" --model-dir "$model_dir" --device "$device"
if [ -n "$reference" ]; then
  set -- "$@" --reference "$reference"
fi
"$@"
