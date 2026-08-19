#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "./.."
.venv-chatterbox/bin/python - <<PY
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="ResembleAI/chatterbox-nano",
    local_dir="models/chatterbox-nano",
    allow_patterns=["*.safetensors", "*.json", "*.txt", "*.pt", "*.model", "*.yaml"],
)
PY
