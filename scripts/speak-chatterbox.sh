#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "./.."
text=Hello from Chatterbox Nano.
out=audio/chatterbox-nano.wav
model=nano
model_dir=models/chatterbox-nano
device=mps
reference=
set -- .venv-chatterbox/bin/python src/chatterbox_tts.py ""   --output "" --model "" --model-dir "" --device ""
if [ -n "" ]; then
  set -- "" --reference ""
fi
""
