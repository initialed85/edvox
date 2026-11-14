#!/usr/bin/env python3
"""Steady-state benchmark against the resident audio.cpp server.

The CLI harness pays a full model load on every line, which makes its RTF incomparable to
a resident service. This drives /v1/audio/speech with models kept loaded, and records
time-to-first-byte as well as total: for a conversational assistant the listener hears the
first byte, not the whole utterance, so TTFB is the number that decides whether a model is
usable on this node.

Usage: run_resident.py <package_id> <voice-name>
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time

SRV = "http://127.0.0.1:8090"
CORPUS = "/audio/ab/corpus.tsv"
FIELDS = ["model", "line", "ref", "audio_seconds", "ttfb_seconds", "wall_seconds",
          "rtf", "realtime", "status", "notes"]


def load_model(pkg: str) -> None:
    r = subprocess.run(["curl", "-s", "-m", "300", "-X", "POST", f"{SRV}/v1/models/load",
                        "-H", "content-type: application/json",
                        "-d", json.dumps({"id": pkg})], capture_output=True, text=True)
    print("load:", r.stdout.strip()[:200], flush=True)


def sample_one(pkg, voice, text, out):
    """POST /v1/audio/speech, streaming to a file, timing the first byte."""
    body = json.dumps({"model": pkg, "input": text, "voice": voice,
                       "response_format": "wav"})
    # curl -o writes as bytes arrive; poll the file for first-byte time.
    p = subprocess.Popen(["curl", "-s", "-m", "600", "-o", out, "-w", "%{http_code}",
                          "-X", "POST", f"{SRV}/v1/audio/speech",
                          "-H", "content-type: application/json", "-d", body],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    t0 = time.monotonic()
    ttfb = None
    while p.poll() is None:
        try:
            if __import__("os").path.getsize(out) > 44:
                ttfb = time.monotonic() - t0
                break
        except OSError:
            pass
        time.sleep(0.05)
    code = (p.stdout.read() or "").strip()
    p.wait()
    return time.monotonic() - t0, ttfb, code


def main() -> int:
    pkg = sys.argv[1]
    voice = sys.argv[2] if len(sys.argv) > 2 else "aussie-conv-ref"
    load_model(pkg)
    out_dir = f"/audio/ab/{pkg}"
    import os
    os.makedirs(out_dir, exist_ok=True)
    # speech writes under the server's roots; ask for a file inside the audio PVC so the
    # response body and the on-disk artefact are the same object.
    rows = []
    with open(CORPUS) as fh:
        corpus = [tuple(l.rstrip("\n").split("\t", 1)) for l in fh if l.strip()]
    # warm the model once outside the measured loop
    warm = subprocess.run(["curl", "-s", "-m", "600", "-o", "/dev/null", "-X", "POST",
                           f"{SRV}/v1/audio/speech", "-H", "content-type: application/json",
                           "-d", json.dumps({"model": pkg, "input": "Warming up.",
                                             "voice": voice, "response_format": "wav"})],
                          capture_output=True, text=True)
    for lid, text in corpus:
        out = f"{out_dir}/{pkg}-{lid}-{voice}.wav"
        wall, ttfb, code = sample_one(pkg, voice, text, out)
        audio = 0.0
        try:
            import torchaudio
            x, sr = torchaudio.load(out)
            audio = x.shape[-1] / sr
        except Exception:  # noqa: BLE001
            pass
        rows.append({"model": pkg, "line": lid, "ref": voice,
                     "audio_seconds": round(audio, 3),
                     "ttfb_seconds": round(ttfb, 3) if ttfb else "",
                     "wall_seconds": round(wall, 2),
                     "rtf": round(wall / audio, 3) if audio else "",
                     "realtime": "yes" if audio and ttfb and ttfb < 0.35 else "no",
                     "status": "ok" if code == "200" and audio else f"http_{code}",
                     "notes": ""})
        print(f"  {lid}: audio={audio:.2f}s ttfb={ttfb} wall={wall:.1f} "
              f"rtf={wall/audio if audio else 0:.2f} http={code}", flush=True)
    m = "/audio/ab/metrics-resident.csv"
    new = not __import__("os").path.exists(m)
    with open(m, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
