#!/usr/bin/env python3
"""Resident Chatterbox HTTP service for repeated local synthesis."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ReferenceKey = tuple[str, int, int]


class State:
    def __init__(self, model, model_name: str, device: str, reference_cache_size: int) -> None:
        self.model = model
        self.model_name = model_name
        self.device = device
        self.lock = threading.Lock()
        # Conditionals are the relatively small, voice-specific result of
        # processing a reference clip. Keep them with the resident model so
        # subsequent turns do not decode the reference audio again.
        self.reference_cache: OrderedDict[ReferenceKey, Any] = OrderedDict()
        self.reference_cache_size = reference_cache_size
        self.active_reference: ReferenceKey | None = None

    def use_reference(self, reference: Path) -> bool:
        """Install reference conditionals and return whether they were cached."""
        resolved = reference.expanduser().resolve()
        info = resolved.stat()
        key: ReferenceKey = (str(resolved), info.st_mtime_ns, info.st_size)
        conditionals = self.reference_cache.get(key)
        if conditionals is not None:
            self.reference_cache.move_to_end(key)
            self.model.conds = conditionals
            self.active_reference = key
            return True

        # This call performs the expensive audio decode, resampling, speaker
        # embedding, and conditioning-token extraction exactly once per cache
        # key. Chatterbox stores the result in model.conds.
        self.model.prepare_conditionals(str(resolved), exaggeration=0.0)
        conditionals = self.model.conds
        if conditionals is None:
            raise RuntimeError("Chatterbox did not produce voice conditionals")
        self.reference_cache[key] = conditionals
        self.reference_cache.move_to_end(key)
        self.active_reference = key

        while len(self.reference_cache) > self.reference_cache_size:
            self.reference_cache.popitem(last=False)
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "voice-chat-chatterbox/1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"chatterbox-server: {fmt % args}", file=sys.stderr, flush=True)

    def send_json(self, status: int, value: dict[str, object]) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            state: State = self.server.state  # type: ignore[attr-defined]
            with state.lock:
                active_reference = state.active_reference[0] if state.active_reference else None
                cached_references = len(state.reference_cache)
            self.send_json(200, {
                "status": "ok",
                "ready": state.model is not None,
                "model": state.model_name,
                "device": state.device,
                "cached_references": cached_references,
                "active_reference": active_reference,
            })
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/synthesize":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            text = str(request.get("text", "")).strip()
            output = Path(str(request.get("output", ""))).expanduser()
            reference = str(request.get("reference", "")).strip()
            if not text:
                raise ValueError("text is required")
            if not output.is_absolute():
                output = ROOT / output

            reference_path: Path | None = None
            if reference:
                reference_path = Path(reference).expanduser()
                if not reference_path.is_file():
                    raise FileNotFoundError(f"reference audio not found: {reference}")

            state: State = self.server.state  # type: ignore[attr-defined]
            started = time.monotonic()
            # Chatterbox generation is not safely concurrent on one model/device.
            with state.lock:
                reference_cached = False
                if reference_path is not None:
                    reference_cached = state.use_reference(reference_path)
                    if reference_cached:
                        self.log_message("reusing cached voice reference: %s", reference_path)
                    else:
                        self.log_message("prepared and cached voice reference: %s", reference_path)

                # Do not pass audio_prompt_path here. use_reference() has
                # already installed the resident conditionals, and passing the
                # path would make Chatterbox recompute them on every request.
                wav = state.model.generate(text)
                import torchaudio as ta
                output.parent.mkdir(parents=True, exist_ok=True)
                ta.save(str(output), wav.detach().cpu(), state.model.sr)
            elapsed = time.monotonic() - started
            seconds = wav.shape[-1] / state.model.sr
            self.send_json(200, {
                "status": "ok",
                "output": str(output),
                "audio_seconds": round(seconds, 3),
                "generation_seconds": round(elapsed, 3),
                "reference_cached": reference_cached if reference_path is not None else None,
            })
        except Exception as error:  # noqa: BLE001 - return service errors to client
            self.log_message("synthesis failed: %s", error)
            self.send_json(500, {"error": str(error)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("nano", "turbo"), default=os.environ.get("CHATTERBOX_MODEL", "nano"))
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--device", default=os.environ.get("CHATTERBOX_DEVICE", "mps"))
    parser.add_argument("--host", default=os.environ.get("TTS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TTS_PORT", "8190")))
    parser.add_argument(
        "--reference-cache-size",
        type=int,
        default=int(os.environ.get("TTS_REFERENCE_CACHE_SIZE", "4")),
        help="Number of processed voice references to retain (default: 4)",
    )
    args = parser.parse_args()
    if args.reference_cache_size < 1:
        parser.error("--reference-cache-size must be at least 1")
    model_dir = Path(args.model_dir or os.environ.get(
        "CHATTERBOX_MODEL_DIR", str(ROOT / "models" / f"chatterbox-{args.model}"),
    )).expanduser()
    if not model_dir.is_dir():
        raise FileNotFoundError(f"Chatterbox model directory not found: {model_dir}")

    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    device = args.device
    if device == "mps" and not torch.backends.mps.is_available():
        print("MPS is unavailable; using CPU", file=sys.stderr)
        device = "cpu"
    print(f"Loading resident Chatterbox {args.model.title()} on {device}...", file=sys.stderr, flush=True)
    started = time.monotonic()
    model = ChatterboxTurboTTS.from_local(model_dir, device=device, nano=args.model == "nano")
    print(f"Chatterbox ready in {time.monotonic() - started:.1f}s on {args.host}:{args.port}", file=sys.stderr, flush=True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = State(model, args.model, device, args.reference_cache_size)  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
