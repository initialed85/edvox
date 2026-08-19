#!/usr/bin/env python3
"""Resident Chatterbox HTTP service for repeated local synthesis."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class State:
    def __init__(self, model, model_name: str, device: str) -> None:
        self.model = model
        self.model_name = model_name
        self.device = device
        self.lock = threading.Lock()


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
            self.send_json(200, {
                "status": "ok",
                "ready": state.model is not None,
                "model": state.model_name,
                "device": state.device,
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
            if reference and not Path(reference).expanduser().is_file():
                raise FileNotFoundError(f"reference audio not found: {reference}")

            state: State = self.server.state  # type: ignore[attr-defined]
            started = time.monotonic()
            # Chatterbox generation is not safely concurrent on one model/device.
            with state.lock:
                wav = state.model.generate(
                    text,
                    audio_prompt_path=reference or None,
                )
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
    args = parser.parse_args()
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
    server.state = State(model, args.model, device)  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
