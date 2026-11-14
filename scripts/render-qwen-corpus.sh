#!/bin/sh
# Renders the Phase 0 corpus through the resident production Qwen daemon so the blind
# listening set compares every candidate on identical text.
#
# ttsd writes only under TTS_ALLOW_ROOTS (/app/edvox/audio), which is the PVC 'audio'
# subPath; the bench pod mounts that same subPath at /audio. So the daemon is given the
# /app/edvox path and the file appears to us under /audio. Output stays on the PVC and
# never enters git.
set -eu
SRV=${1:-http://edvox-tts.edvox.svc.cluster.local:8080}
CORPUS=${2:-/audio/ab/corpus.tsv}
LOCAL_DIR=${3:-/audio/ab/qwen_prod}
REMOTE_DIR=${4:-/app/edvox/audio/ab/qwen_prod}
REF_REMOTE=${5:-/app/edvox/voices/clone/aussie-conv-ref.wav}
mkdir -p "$LOCAL_DIR"
while IFS=$(printf '\t') read -r id text; do
  [ -n "${id:-}" ] || continue
  local_out="$LOCAL_DIR/qwen_prod-$id-conv.wav"
  remote_out="$REMOTE_DIR/qwen_prod-$id-conv.wav"
  t0=$(date +%s%N)
  code=$(python3 - "$text" "$remote_out" "$REF_REMOTE" <<'PY'
import json, subprocess, sys
text, out, ref = sys.argv[1:4]
body = json.dumps({"text": text, "output": out, "reference": ref,
                   "lang": "en", "frames": 0})
p = subprocess.run(["curl", "-s", "-m", "300", "-o", "/dev/null", "-w", "%{http_code}",
                    "-X", "POST", "http://edvox-tts.edvox.svc.cluster.local:8080/synthesize",
                    "-H", "content-type: application/json", "-d", body],
                   capture_output=True, text=True)
print(p.stdout.strip())
PY
)
  t1=$(date +%s%N)
  echo "$id http=$code wall_ms=$(( (t1-t0)/1000000 )) $(ls -l "$local_out" 2>/dev/null | awk '{print "bytes:",$5}')"
done < "$CORPUS"
echo QWENDONE
