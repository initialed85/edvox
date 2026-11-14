#!/usr/bin/env python3
"""Phase 1 follow-up 2: ChatterboxMultilingualTTS - accent AND non-verbal sounds?

Ed rejected Turbo on accent grounds: with aussie-conv-ref it is "marginally more British"
than standard Chatterbox, and with aussie-expressive-ref it is "basically a standard
American accent". Standard Chatterbox has the accent he wants but, per
scripts/accent_probe.py, no paralinguistic vocabulary in its base tokenizer.

That conclusion was drawn from tokenizer.json, which is the wrong file for this checkpoint.
The standard repo also ships mtl_tokenizer.json + t3_mtl23ls_v3.safetensors, loaded by
chatterbox.mtl_tts.ChatterboxMultilingualTTS, whose added_tokens include 31 non-verbal
events ([giggle], [laughter], [guffaw], [sigh], [whisper], [gasp], [mumble],
[clear_throat], ...). If that path keeps the standard accent and renders the tokens as
non-speech, the accent/expressiveness tradeoff disappears.

Risk being measured, not assumed: MTL is a 23-language checkpoint, so the accent may not be
the same voice as 04-G07, and the standard model already costs RTF 5.4-9.0 against Turbo's
2.2-2.6. Every render is transcribed by the production whisper service so a token that got
read as a *word* is visible in the output rather than hidden in a waveform.
"""
import json
import os
import time

import numpy as np
import requests
import torch
import torchaudio

OUT = "/audio/ab/mtl"
REF = "/audio/voices/clone/aussie-conv-ref.wav"
REF_EXPR = "/audio/voices/clone/aussie-expressive-ref.wav"
WHISPER = "http://edvox-whisper.edvox.svc.cluster.local:8178/inference"

CASES = [
    ("04-plain-conv", "en", "Yeah nah, she'll be right — we'll grab a servo pie and sort it out after the arvo.", REF, 0.5),
    ("04-plain-expr", "en", "Yeah nah, she'll be right — we'll grab a servo pie and sort it out after the arvo.", REF_EXPR, 0.5),
    ("03-laugh-conv", "en", "Well [laughter], that went about as well as expected.", REF, 0.5),
    ("03-laugh-x15", "en", "Well [laughter], that went about as well as expected.", REF, 1.5),
    ("03-giggle-x15", "en", "Well [giggle], that went about as well as expected.", REF, 1.5),
    ("05-sigh-whisper", "en", "[sigh] I suppose that is fine. [whisper] Do not tell anyone.", REF, 0.8),
]


def rss_mb():
    with open("/proc/self/status") as fh:
        for ln in fh:
            if ln.startswith("VmRSS:"):
                return int(ln.split()[1]) // 1024
    return -1


def transcribe(path, sr):
    """Ask the production STT what it hears, so a spoken-as-word tag is unmissable."""
    x, _ = torchaudio.load(path)
    x = torchaudio.functional.resample(x, sr, 16000).mean(0)
    x = (x * 32767).clamp(-32768, 32767).to(torch.int16).unsqueeze(0)
    torchaudio.save("/tmp/w16k.wav", x, 16000, encoding="PCM_S", bits_per_sample=16)
    with open("/tmp/w16k.wav", "rb") as fh:
        r = requests.post(WHISPER, files={"file": fh},
                          data={"language": "en", "response_format": "json"}, timeout=180)
    try:
        return r.json().get("text", "").strip()
    except Exception:
        return "UNTRANSCRIBED(%s)" % r.status_code


os.makedirs(OUT, exist_ok=True)
from chatterbox.mtl_tts import ChatterboxMultilingualTTS as M

t0 = time.time()
model = M.from_local("/models/chatterbox", "cpu")
sr = model.sr
print("MTL loaded in %.1fs sr=%s rss=%dMB" % (time.time() - t0, sr, rss_mb()), flush=True)

rows = []
for name, lang, text, ref, exag in CASES:
    t1 = time.time()
    try:
        wav = model.generate(text, lang, audio_prompt_path=ref, exaggeration=exag)
    except Exception as exc:
        print(f"  {name} FAILED: {type(exc).__name__}: {exc}", flush=True)
        rows.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
        continue
    wall = time.time() - t1
    x = wav.squeeze().float().cpu()
    raw_peak = float(x.abs().max())
    if raw_peak > 0.988:
        x = x * (0.891 / raw_peak)
    path = f"{OUT}/{name}.wav"
    torchaudio.save(path, x.unsqueeze(0), sr, encoding="PCM_S", bits_per_sample=16)
    dur = x.shape[0] / sr
    heard = transcribe(path, sr)
    rows.append({"name": name, "lang": lang, "ref": os.path.basename(ref), "exaggeration": exag,
                 "text": text, "audio_s": round(dur, 2), "wall_s": round(wall, 2),
                 "rtf": round(wall / dur, 2), "raw_peak": round(raw_peak, 3),
                 "rss_mb": rss_mb(), "whisper_heard": heard,
                 "tag_read_as_word": bool(text.count("[") and any(
                     t in heard.lower() for t in ("laughter", "giggle", "sigh", "whisper", "guffaw")))})
    print(f"  {name} audio={dur:.2f}s wall={wall:.2f}s RTF={wall/dur:.2f} peak={raw_peak:.3f} "
          f"rss={rows[-1]['rss_mb']}MB\n    whisper: {heard!r}  tag_as_word={rows[-1]['tag_read_as_word']}",
          flush=True)

with open(f"{OUT}/mtl-metrics.json", "w") as fh:
    json.dump(rows, fh, indent=1)
print("WROTE", f"{OUT}/mtl-metrics.json")
