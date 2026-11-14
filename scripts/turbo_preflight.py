#!/usr/bin/env python3
"""Phase 2 pre-flight: confirm the accent holds at the setting we are shipping.

Ed's chosen clip is samples/ab/tag/chatterbox_turbo_upstream-03-tagged-x15.wav - Chatterbox
Turbo under upstream Python, aussie-conv-ref.wav, exaggeration=1.5, text containing the
[chuckle] token - and he rates both the accent and the non-verbal sound good on it.

That is line 03, "Well [chuckle], that went about as well as expected", which carries no
Australian markers at all. The accent failure was observed on line 04, the idiom line, and it
was measured at the library default exaggeration (0.5), not at 1.5. So the config being
shipped has not been heard on the hardest accent test that exists.

This script renders line 04 and the two most idiomatic follow-ups at exaggeration 0.5 (what
was measured) and 1.5 (what is shipping), plus a tag render at 1.5, against the same
reference, and transcribes each with the production whisper service so dropped or invented
words are visible in the metrics rather than only in the waveform.

Every output is peak-guarded to ~-1 dBFS because exaggeration 1.5 measured raw peaks of both
0.765 and 1.015 across renders - the dial does not map monotonically onto amplitude.
"""
import json
import os
import time

import requests
import torch
import torchaudio

OUT = "/audio/ab/preflight"
REF = "/audio/voices/clone/aussie-conv-ref.wav"
WHISPER = "http://edvox-whisper.edvox.svc.cluster.local:8178/inference"

LINES = {
    "04-idiom": "Yeah nah, she'll be right — we'll grab a servo pie and sort it out after the arvo.",
    "06-ute": "Throw it in the ute, we'll knock it over before the barbie, no worries.",
    "07-plain": "Sorry, I did not catch that — could you say it again?",
}
CASES = [(l, x, False) for l in LINES for x in (0.5, 1.5)] + [
    ("04-idiom", 1.5, True), ("07-plain", 1.5, True),
]


def tag(text):
    return text.replace("Yeah nah,", "Yeah nah [chuckle],").replace("Sorry,", "Sorry [chuckle],")


def rss_mb():
    with open("/proc/self/status") as fh:
        for ln in fh:
            if ln.startswith("VmRSS:"):
                return int(ln.split()[1]) // 1024
    return -1


def transcribe(path, sr):
    x, _ = torchaudio.load(path)
    x = torchaudio.functional.resample(x, sr, 16000).mean(0)
    x = (x * 32767).clamp(-32768, 32767).to(torch.int16).unsqueeze(0)
    torchaudio.save("/tmp/pf16k.wav", x, 16000, encoding="PCM_S", bits_per_sample=16)
    with open("/tmp/pf16k.wav", "rb") as fh:
        r = requests.post(WHISPER, files={"file": fh},
                          data={"language": "en", "response_format": "json"}, timeout=180)
    try:
        return r.json().get("text", "").strip()
    except Exception:
        return "UNTRANSCRIBED(%s)" % r.status_code


os.makedirs(OUT, exist_ok=True)
from chatterbox.tts_turbo import ChatterboxTurboTTS as T

t0 = time.time()
model = T.from_local("/models/chatterbox-turbo", "cpu")
sr = model.sr
print(f"Turbo loaded in {time.time()-t0:.1f}s sr={sr} rss={rss_mb()}MB", flush=True)

rows = []
for line, exag, tagged in CASES:
    text = tag(LINES[line]) if tagged else LINES[line]
    name = f"{line}-x{str(exag).replace('.','')}-{'tagged' if tagged else 'plain'}"
    t1 = time.time()
    wav = model.generate(text, audio_prompt_path=REF, exaggeration=exag)
    wall = time.time() - t1
    x = wav.squeeze().float().cpu()
    raw = float(x.abs().max())
    if raw > 0.988:
        x = x * (0.891 / raw)
    path = f"{OUT}/{name}.wav"
    torchaudio.save(path, x.unsqueeze(0), sr, encoding="PCM_S", bits_per_sample=16)
    dur = x.shape[0] / sr
    heard = transcribe(path, sr)
    rows.append({"name": name, "line": line, "exaggeration": exag, "tagged": tagged,
                 "text": text, "audio_s": round(dur, 2), "wall_s": round(wall, 2),
                 "rtf": round(wall / dur, 2), "raw_peak": round(raw, 3), "guarded_peak": 0.891,
                 "rss_mb": rss_mb(), "whisper_heard": heard})
    print(f"  {name} audio={dur:.2f}s wall={wall:.2f}s RTF={wall/dur:.2f} rawpeak={raw:.3f}\n"
          f"    whisper: {heard!r}", flush=True)

with open(f"{OUT}/preflight.json", "w") as fh:
    json.dump(rows, fh, indent=1)
print("WROTE", f"{OUT}/preflight.json")
