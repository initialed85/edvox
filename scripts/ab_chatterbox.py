#!/usr/bin/env python3
"""Phase 0 Chatterbox harness using the upstream Resemble runtime.

Needed because audio.cpp's Chatterbox integration exposes voice cloning but not the
paralinguistic tag interface (it reads "[chuckle]" as the word "chuckle"), and because
audio.cpp has no Chatterbox Nano package at all. This is the sanctioned fallback in
docs/tts-changeover-spec.md section 9, trap T1: the harness lives in scripts/, not src/.

The model is loaded once and reused, so wall_seconds here is generation-only and is
directly comparable to the resident production daemon. Peak RSS is reported for the whole
process rather than per request, which is the honest figure for a resident server.

Usage: ab_chatterbox.py <nano|turbo> [model_dir]
"""
from __future__ import annotations

import argparse
import csv
import os
import resource
import sys
import time
from pathlib import Path

CORPUS = "/audio/ab/corpus.tsv"
OUT_ROOT = "/audio/ab"
REF_PRIMARY = "/audio/voices/clone/aussie-conv-ref.wav"
REF_EXPRESSIVE = "/audio/voices/clone/aussie-expressive-ref.wav"
FIELDS = ["model", "line", "ref", "audio_seconds", "wall_seconds", "rtf",
          "peak_rss_mb", "status", "notes"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model", choices=("nano", "turbo"))
    ap.add_argument("model_dir", nargs="?", default=None)
    args = ap.parse_args()

    model_dir = args.model_dir or f"/models/chatterbox-{args.model}"
    if not Path(model_dir).is_dir():
        print(f"model directory not found: {model_dir}", file=sys.stderr)
        return 3

    import torch
    import torchaudio as ta
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    torch.set_num_threads(int(os.environ.get("AB_THREADS", "8")))
    code = f"chatterbox_{args.model}_upstream"
    os.makedirs(f"{OUT_ROOT}/{code}", exist_ok=True)

    print(f"loading Chatterbox {args.model} from {model_dir} on cpu", flush=True)
    t_load = time.monotonic()
    # chatterbox-tts 0.1.7 signature is from_local(ckpt_dir, device); there is no `nano`
    # flag - nano vs turbo is chosen by which checkpoint directory you point it at.
    model = ChatterboxTurboTTS.from_local(model_dir, "cpu")
    load_s = time.monotonic() - t_load
    rss_after_load = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print(f"loaded in {load_s:.1f}s, rss {rss_after_load:.0f}MB, sr={model.sr}", flush=True)

    rows = []
    import inspect
    sig = inspect.signature(model.generate)
    caps_note = "generate_args=" + ",".join(
        p2 for p2 in sig.parameters if p2 not in ("self", "text"))[:180]

    with open(CORPUS) as fh:
        corpus = [tuple(l.rstrip("\n").split("\t", 1)) for l in fh if l.strip()]

    # exaggeration is Chatterbox's expressiveness dial; 0.3 is the cautious default and
    # 0.8 is pushed high. Tag line (03) is where the whole decision turns.
    for lid, text in corpus:
        variants = [(REF_PRIMARY, "conv", 0.3)]
        if lid == "01":
            variants += [(REF_EXPRESSIVE, "expressive", 0.3), (REF_PRIMARY, "conv-x08", 0.8)]
        if lid == "03":
            variants.append((REF_PRIMARY, "conv-x08", 0.8))
        for reference, ref_label, exaggeration in variants:
            out = f"{OUT_ROOT}/{code}/{code}-{lid}-{ref_label}.wav"
            status, notes = "ok", ""
            t0 = time.monotonic()
            try:
                wav = model.generate(text, audio_prompt_path=reference,
                                     exaggeration=exaggeration)
                ta.save(out, wav.detach().cpu(), model.sr)
            except Exception as exc:  # noqa: BLE001
                status, notes = "error", repr(exc)[:140]
                wav = None
            wall = time.monotonic() - t0
            audio = (wav.shape[-1] / model.sr) if wav is not None else None
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
            rows.append({"model": code, "line": lid, "ref": ref_label,
                         "audio_seconds": round(audio, 3) if audio else "",
                         "wall_seconds": round(wall, 2),
                         "rtf": round(wall / audio, 3) if audio else "",
                         "peak_rss_mb": round(rss), "status": status,
                         "notes": (notes or f"exagg={exaggeration} {caps_note}")})
            print(f"  {lid}/{ref_label}: audio={audio} gen={wall:.1f}s "
                  f"rtf={wall/audio if audio else 0:.2f} rss={rss:.0f}MB {status} {notes}",
                  flush=True)

    metrics = f"{OUT_ROOT}/metrics-upstream.csv"
    is_new = not Path(metrics).exists()
    with open(metrics, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if is_new:
            w.writeheader()
        w.writerows(rows)
    print(f"load_seconds={load_s:.2f} peak_rss_mb={rss_after_load:.0f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
