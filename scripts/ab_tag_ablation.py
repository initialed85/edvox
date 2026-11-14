#!/usr/bin/env python3
"""Tag-ablation probe for upstream Chatterbox Turbo.

The corpus only contains the tagged form of line 03, so there is nothing to compare it
against. This loads the model once and renders line 03 three ways - tagged, stripped, and
tagged with high exaggeration - so the tag's audible contribution can be measured rather
than guessed at, and so the exaggeration dial has a same-text control.
"""
from __future__ import annotations

import json
import sys
import time

import torch
import torchaudio as ta
from chatterbox.tts_turbo import ChatterboxTurboTTS

REF = "/audio/voices/clone/aussie-conv-ref.wav"
OUT = "/audio/ab/chatterbox_turbo_upstream"

# Texts match corpus.tsv exactly where they overlap, so these are directly comparable to
# the other candidates' line 01 / line 03 renders.
CASES = [
    ("03-tagged", "Well [chuckle], that went about as well as expected."),
    ("03-stripped", "Well, that went about as well as expected."),
    ("03-tagged-x08", "Well [chuckle], that went about as well as expected."),
    ("03-word", "Well chuckle, that went about as well as expected."),
    ("03-tagged-x15", "Well [chuckle], that went about as well as expected."),
    ("01-laugh-tag", "Oh, joy. [laugh] Another riveting exchange awaits."),
    ("01-sarcastic", "[sarcastic] Oh, joy. Another riveting exchange awaits."),
]


def main() -> int:
    torch.set_num_threads(8)
    model = ChatterboxTurboTTS.from_local("/models/chatterbox-turbo", "cpu")
    rows = []
    for name, text in CASES:
        exagg = 0.8 if "x08" in name else (1.5 if "x15" in name else 0.3)
        t0 = time.monotonic()
        wav = model.generate(text, audio_prompt_path=REF, exaggeration=exagg)
        wall = time.monotonic() - t0
        dur = wav.shape[-1] / model.sr
        path = f"{OUT}/chatterbox_turbo_upstream-{name}.wav"
        ta.save(path, wav.detach().cpu(), model.sr)
        peak = float(wav.abs().max())
        rows.append({"case": name, "text": text, "exaggeration": exagg,
                     "seconds": round(dur, 3), "gen_s": round(wall, 2),
                     "rtf": round(wall / dur, 3), "peak": round(peak, 4)})
        print(f"{name:18s} dur={dur:5.2f}s gen={wall:5.1f}s rtf={wall/dur:.2f} peak={peak:.3f}",
              flush=True)
    with open(f"{OUT}/tag-ablation.json", "w") as fh:
        json.dump(rows, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
