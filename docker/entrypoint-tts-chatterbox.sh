#!/bin/sh
# Entrypoint for the resident Chatterbox TTS service.
#
# Every default here is a measured value, not a library default, so the reasoning is
# written next to each one.
set -eu

MODEL=${CHATTERBOX_MODEL:-turbo}
MODEL_DIR=${CHATTERBOX_MODEL_DIR:-/models/chatterbox-$MODEL}
DEVICE=${CHATTERBOX_DEVICE:-cpu}
PORT=${TTS_PORT:-8190}
ALLOW_ROOTS=${TTS_ALLOW_ROOTS:-/app/edvox/audio:/app/edvox/voices}
WARMUP_REFERENCE=${TTS_WARMUP_REFERENCE:-/app/edvox/voices/clone/aussie-conv-ref.wav}

# temperature 0.5, NOT the library default 0.8. Measured on the idiom line through this
# service over HTTP, same reference and text: 0.8 rendered "servo pie"+"arvo" correctly in
# 2 of 6 turns (one turned "she'll be right" into "shall we write"), 0.5 in 4 of 4. Word
# accuracy, not taste - 0.8 is not shippable. Revisit against the RTX 2080 only if the GPU
# run makes sampling cheaper than the accuracy loss.
TEMPERATURE=${CHATTERBOX_TEMPERATURE:-0.5}

# repetition_penalty is left at the library 1.2: it was not measured during the changeover,
# so it is passed through unchanged rather than guessed at.
REPETITION_PENALTY=${CHATTERBOX_REPETITION_PENALTY:-1.2}

# Kept for the standard/MTL checkpoints, which do have a trained emotion_adv_fc. On Turbo it
# is inert - the checkpoint carries no emotion_adv weight and cond_enc.emotion_adv_fc is None,
# so with a fixed seed 0.0 and 2.0 produce bit-identical audio. The service reports
# exaggeration_is_inert_on_this_model rather than letting this line imply it does something.
EXAGGERATION=${CHATTERBOX_EXAGGERATION:-1.5}

echo "edvox tts-chatterbox: model=$MODEL dir=$MODEL_DIR device=$DEVICE temp=$TEMPERATURE exaggeration=$EXAGGERATION"

if [ ! -d "$MODEL_DIR" ]; then
  echo "edvox tts-chatterbox: FATAL model directory $MODEL_DIR is empty - the weights init container failed" >&2
  exit 1
fi

# torch on 8 thin cores: the synthesis is CPU-bound and the pod is pinned to the node with
# the idle cores, so let torch use them but never oversubscribe the desktop session.
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-8}

exec python3 /app/edvox/src/chatterbox_server.py \
  --model "$MODEL" \
  --model-dir "$MODEL_DIR" \
  --device "$DEVICE" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --temperature "$TEMPERATURE" \
  --repetition-penalty "$REPETITION_PENALTY" \
  --exaggeration "$EXAGGERATION" \
  --allow-roots "$ALLOW_ROOTS" \
  --warmup-reference "$WARMUP_REFERENCE" \
  --reference-cache-size "${TTS_REFERENCE_CACHE_SIZE:-4}"
