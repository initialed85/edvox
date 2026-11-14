#!/usr/bin/env python3
"""Build the Phase 0 blind listening set on the PVC.

Every candidate renders the same text, so the only thing the ear should be judging is the
voice itself. Labels are randomised and the answer key is written next to the clips on the
PVC - never into git, because these are clones of a real, identifiable person.

Run inside the bench pod (ttsab): python3 /audio/ab/make_blind_set.py
"""
from __future__ import annotations

import os
import random
import shutil

SRC = {
    # model -> directory
    "qwen3-tts-prod": "/audio/ab/qwen_prod",
    "chatterbox-gguf": "/audio/ab/chatterbox",
    "chatterbox-turbo-gguf": "/audio/ab/chatterbox_turbo",
    "cosyvoice3-gguf": "/audio/ab/cosyvoice3",
    "index-tts2.5-gguf": "/audio/ab/index_tts2_5",
    "chatterbox-turbo-python": "/audio/ab/chatterbox_turbo_upstream",
}
# (line, per-model filename suffix). Line 03 exercises the tag, line 04 the accent.
TARGETS = [("01", "conv"), ("03", "conv"), ("04", "conv"), ("05", "conv")]

OUT = "/audio/ab/listen"
random.seed(20261114)


def find(model: str, line: str, ref: str) -> str | None:
    d = SRC[model]
    cands = [
        f"{d}/{model}-{line}-{ref}.wav".replace("qwen3-tts-prod", "qwen_prod"),
        f"{d}/{line}-{ref}.wav",
    ]
    # the harnesses each named their files slightly differently; glob as a fallback
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(f"-{line}-{ref}.wav") and fn.endswith(".wav"):
                cands.insert(0, os.path.join(d, fn))
    for c in cands:
        if os.path.isfile(c) and os.path.getsize(c) > 2000:
            return c
    return None


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    items = []
    for line, ref in TARGETS:
        for model in SRC:
            path = find(model, line, ref)
            if path:
                items.append((line, model, path))
    random.shuffle(items)
    key = []
    for i, (line, model, path) in enumerate(items, start=1):
        label = f"{line}-{'ABCDEFGHJKLMNPQRSTUVWXYZ'[((i - 1) % 24)]}{i:02d}"
        dst = f"{OUT}/{label}.wav"
        shutil.copyfile(path, dst)
        key.append((label, model, line, ref, os.path.getsize(dst)))
    with open(f"{OUT}/KEY.txt", "w") as fh:
        fh.write("# Phase 0 blind listening key. PVC only - do not commit.\n")
        fh.write("# label\tmodel\tline\tref\tbytes\n")
        for row in key:
            fh.write("\t".join(str(x) for x in row) + "\n")
    print(f"built {len(key)} clips in {OUT}")
    for row in key:
        print("  ", row[0], row[1], f"{row[4]} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
