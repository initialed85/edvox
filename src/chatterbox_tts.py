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
    parser.add_argument("--model-dir", default=os.environ.get("CHATTERBOX_MODEL_DIR", "models/chatterbox-nano"))
    parser.add_argument("--model", choices=("nano", "turbo"), default=os.environ.get("CHATTERBOX_MODEL", "nano"))
    args = parser.parse_args()

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
    model = ChatterboxTurboTTS.from_local(
        model_dir, device=device, nano=args.model == "nano"
    )
    print(f"Model loaded in {time.monotonic() - started:.1f}s", file=sys.stderr)

    reference = args.reference or None
    if reference:
        reference = str(Path(reference).expanduser())
        if not Path(reference).is_file():
            raise FileNotFoundError(f"reference audio not found: {reference}")
        print(f"Using voice reference: {reference}", file=sys.stderr)

    started = time.monotonic()
    wav = model.generate(args.text, audio_prompt_path=reference)
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    ta.save(str(output), wav.detach().cpu(), model.sr)
    print(f"Generated {wav.shape[-1] / model.sr:.2f}s in {time.monotonic() - started:.1f}s", file=sys.stderr)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
