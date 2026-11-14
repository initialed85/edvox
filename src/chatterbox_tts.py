#!/usr/bin/env python3
"""Generate a WAV file with Chatterbox Nano or Turbo."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text")
    parser.add_argument("--output", "-o", default="audio/chatterbox.wav")
    parser.add_argument("--reference", default=os.environ.get("TTS_SPEAKER_FILE", ""))
    parser.add_argument("--device", default=os.environ.get("CHATTERBOX_DEVICE", "mps"))
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--model", choices=("nano", "turbo"), default=os.environ.get("CHATTERBOX_MODEL", "nano"))
    args = parser.parse_args()
    if args.model_dir is None:
        args.model_dir = os.environ.get("CHATTERBOX_MODEL_DIR", f"models/chatterbox-{args.model}")

    import torch
    import torchaudio as ta
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    device = args.device
    if device == "mps" and not torch.backends.mps.is_available():
        print("MPS is unavailable; using CPU", file=sys.stderr)
        device = "cpu"
    model_dir = Path(args.model_dir).expanduser()
    if not model_dir.is_dir():
        raise FileNotFoundError(f"Chatterbox model directory not found: {model_dir}")

    print(f"Loading Chatterbox {args.model.title()} on {device}...", file=sys.stderr)
    started = time.monotonic()
    # chatterbox-tts 0.1.7 signature is from_local(ckpt_dir, device); there is no `nano`
    # flag. Nano and Turbo are selected by which checkpoint directory is passed, and 0.1.7
    # can only actually load Turbo - ChatterboxTurboTTS hardcodes the turbo T3Config and
    # opens t3_turbo_v1.safetensors, while the nano repo ships t3_nano_v1.safetensors with
    # an incompatible state dict (1 of 155 tensors shape-matches). See
    # docs/tts-phase0-results.md "Corrections to the spec and to our own code".
    model = ChatterboxTurboTTS.from_local(model_dir, device)
    print(f"Model loaded in {time.monotonic() - started:.1f}s", file=sys.stderr)

    reference = args.reference or None
    if reference:
        reference = str(Path(reference).expanduser())
        if not Path(reference).is_file():
            raise FileNotFoundError(f"reference audio not found: {reference}")
        print(f"Using voice reference: {reference}", file=sys.stderr)

    started = time.monotonic()
    # exaggeration is the expressiveness dial. 0.8 and above clip (measured peak 1.005), so
    # the default stays mid-range and is clamped defensively here.
    exaggeration = min(max(float(os.environ.get("CHATTERBOX_EXAGGERATION", "0.5")), 0.0), 0.7)
    wav = model.generate(args.text, audio_prompt_path=reference,
                         exaggeration=exaggeration)
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    ta.save(str(output), wav.detach().cpu(), model.sr)
    print(f"Generated {wav.shape[-1] / model.sr:.2f}s in {time.monotonic() - started:.1f}s", file=sys.stderr)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
