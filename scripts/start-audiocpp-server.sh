#!/bin/sh
# Starts the resident audio.cpp server with a real config: models are declared in
# server.json (the server does not scan models-root for them), each needing id, family and
# path. Keeps the two cloning-capable families resident so steady-state timings are
# comparable to the resident production Qwen daemon.
BIN=/app/audiocpp_server
for p in $(ls /proc | grep -E '^[0-9]+$'); do
  c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
  case "$c" in
    *"$BIN"*) kill -9 "$p" 2>/dev/null ;;
  esac
done
sleep 2
python3 - <<'PY'
import json
models = [
    {"id": "chatterbox_q8_0", "family": "chatterbox",
     "path": "/models/audio.cpp/Chatterbox-GGUF/chatterbox-q8_0.gguf"},
    {"id": "cosyvoice3_q8_0", "family": "cosyvoice3",
     "path": "/models/audio.cpp/CosyVoice3-GGUF/cosyvoice3-q8_0.gguf"},
    {"id": "index_tts2_5_q8_0", "family": "index_tts2",
     "path": "/models/audio.cpp/IndexTTS2.5-GGUF/index-tts2_5-q8_0.gguf"},
]
json.dump({"models": models}, open("/tmp/cfg.json", "w"), indent=2)
PY
ls -l /models/audio.cpp/*/*.gguf 2>/dev/null | head -8
cd /app
export LD_LIBRARY_PATH=/app
nohup "$BIN" --config /tmp/cfg.json --ui --ui-management --backend cpu \
  --models-root /models/audio.cpp --voice-dir /audio/voices/clone \
  --host 0.0.0.0 --port 8090 --threads 8 --max-loaded-models 3 --log \
  > /audio/ab/server.log 2>&1 &
sleep 25
curl -s -m 8 http://127.0.0.1:8090/health
echo
curl -s -m 15 http://127.0.0.1:8090/v1/models | head -c 700
echo
tail -3 /audio/ab/server.log
