#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
printf hostname: ; hostname
printf memory: ; sysctl -n hw.memsize
printf disk: ; df -h "$HOME" | tail -1
printf chat: ; curl -fsS --max-time 3 "${CHAT_BASE_URL:-http://192.168.137.111:8088}/health"; echo
printf llama-tts: ; ./bin/llama-tts --version 2>&1 | head -1
printf whisper: ; ./bin/whisper-cli --version 2>&1 | head -1
