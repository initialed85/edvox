#!/usr/bin/env python3
"""Phase 1 follow-up: is the accent difference a MODEL property or a RUNTIME property?

Blind-set evidence: 04-G07 (Chatterbox standard, GGUF q8_0) "really nails" the Aussie
accent; 04-X22 (Chatterbox Turbo, upstream Python) is "marginally more British". Those two
clips differ in BOTH model and runtime, so the comparison cannot attribute the difference.
This script renders the same two corpus lines with both checkpoints under the same Python
runtime and both surviving reference windows, so model and reference are each isolated.

Also establishes the standard checkpoint's CPU speed, since standard Chatterbox has no
paralinguistic tag vocabulary (added_tokens.json is absent and "chuckle" appears nowhere in
tokenizer.json) and would only be viable as the low-latency/no-control voice.
"""
import json
import os
import time

import numpy as np
import torch
import torchaudio

OUT = "/audio/ab/accent"
REFS = {"conv": "/audio/voices/clone/aussie-conv-ref.wav",
        "expr": "/audio/voices/clone/aussie-expressive-ref.wav"}
LINES = {
    "03": "Well [chuckle], that went about as well as expected.",
    "04": "Yeah nah, she'll be right — we'll grab a servo pie and sort it out after the arvo.",
}
EXAG = 1.5  # Ed's pick; peak-guarded below the same way Phase 2 will do it.


def rss_mb():
    with open("/proc/self/status") as fh:
        for ln in fh:
            if ln.startswith("VmRSS:"):
                return int(ln.split()[1]) // 1024
    return -1



def peak(wav):
    x = wav.squeeze().abs().max().item()
    return x


def save(wav, path, sr):
    x = wav.squeeze().float().cpu()
    p = float(x.abs().max())
    if p > 0.988:                      # Phase 2's peak guard: normalise to ~-1 dBFS
        x = x * (0.891 / p)
    torchaudio.save(path, x.unsqueeze(0), sr, encoding="PCM_S", bits_per_sample=16)
    return p


rows = []
os.makedirs(OUT, exist_ok=True)

for mk, loader in [
    ("std", lambda: __import__("chatterbox.tts", fromlist=["ChatterboxTTS"]).ChatterboxTTS
        .from_local("/models/chatterbox", "cpu")),
    ("turbo", lambda: __import__("chatterbox.tts_turbo", fromlist=["ChatterboxTurboTTS"])
        .ChatterboxTurboTTS.from_local("/models/chatterbox-turbo", "cpu")),
]:
    t0 = time.time()
    model = loader()
    load_s = time.time() - t0
    sr = model.sr
    print(f"[{mk}] loaded in {load_s:.1f}s sr={sr} rss={rss_mb():.0f}MB", flush=True)
    for line, text in LINES.items():
        for rk, ref in REFS.items():
            kw = {"audio_prompt_path": ref}
            if mk == "turbo":
                kw["exaggeration"] = EXAG
            t1 = time.time()
            wav = model.generate(text, **kw)
            wall = time.time() - t1
            dur = wav.squeeze().shape[0] / sr
            name = f"{mk}-{line}-{rk}.wav"
            raw_peak = save(wav, f"{OUT}/{name}", sr)
            rows.append({"name": name, "model": mk, "line": line, "ref": rk,
                         "audio_s": round(dur, 2), "wall_s": round(wall, 2),
                         "rtf": round(wall / dur, 2), "raw_peak": round(raw_peak, 3),
                         "rss_mb": round(rss_mb())})
            print(f"  {name} audio={dur:.2f}s wall={wall:.2f}s RTF={wall/dur:.2f} "
                  f"rawpeak={raw_peak:.3f} rss={rows[-1]['rss_mb']}MB", flush=True)
    del model
    torch.cuda.empty_cache() if torch.cuda.is_available() else None

with open(f"{OUT}/accent-metrics.json", "w") as fh:
    json.dump(rows, fh, indent=1)
print("\nWROTE", f"{OUT}/accent-metrics.json", len(rows), "rows")
