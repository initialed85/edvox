#!/usr/bin/env python3
"""Resident Chatterbox HTTP service for repeated synthesis.

Contract is deliberately the same one `native/ttsd.cpp` serves, so `web_server.py` can
point at either backend without knowing which is which:

    POST /synthesize {"text", "output", "reference", "exaggeration"?}
                   -> {"status","output","audio_seconds","generation_seconds","raw_peak"}
    GET  /health   -> {"status","ready","model","device","exaggeration","cached_references"}

Why the conditionals cache is keyed by exaggeration
---------------------------------------------------
Chatterbox bakes the expressiveness dial into the *voice conditionals*, not into sampling:
`prepare_conditionals()` sets `emotion_adv = exaggeration * ones`, and `generate()` warns that
its own `exaggeration` argument is ignored unless `audio_prompt_path` is passed (which
recomputes conditionals every turn). So wherever the dial does something, it is part of the
identity of a cached conditional set and cannot be a request-time decoration.

On **Turbo the dial is inert**, though. Its t3 checkpoint has no `emotion_adv_fc` weight and
`cond_enc.emotion_adv_fc is None`, so nothing consumes `emotion_adv`: with a fixed seed,
exaggeration 0.0 / 0.5 / 1.5 / 2.0 render *bit-identical* audio (measured; see
docs/tts-phase0-results.md correction 3). The parameter is kept because the standard and MTL
checkpoints do train that projection - it is real there - and because a caller switching
models should not have to change the wire format. It is not a knob worth tuning on Turbo.

Nothing is downloaded here: weights arrive via the init container into CHATTERBOX_MODEL_DIR,
and audio paths are confined to TTS_ALLOW_ROOTS so the service cannot be used to write
outside the shared volume.
"""
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
# (path, mtime_ns, size, exaggeration) -> Conditionals. Exaggeration is part of the identity
# of a conditional set, so it cannot be a request-time decoration.
ReferenceKey = tuple[str, int, int, float]

# Match native/ttsd.cpp: colon-separated roots that outputs and references must live under.
DEFAULT_ALLOW_ROOTS = "/app/edvox/audio:/app/edvox/voices"
# Full-scale ceiling. Renders above this are scaled down rather than left to clip.
PEAK_GUARD_THRESHOLD = 0.988
PEAK_GUARD_TARGET = 0.891  # ~-1 dBFS


def parse_allow_roots(value: str | None) -> list[Path]:
    roots = []
    for part in (value or DEFAULT_ALLOW_ROOTS).split(":"):
        part = part.strip()
        if part:
            roots.append(Path(part).resolve())
    return roots


class State:
    def __init__(self, model, model_name: str, device: str, default_exaggeration: float,
                 reference_cache_size: int, allow_roots: list[Path],
                 default_temperature: float = 0.8,
                 default_repetition_penalty: float = 1.2) -> None:
        self.model = model
        self.model_name = model_name
        self.device = device
        self.default_exaggeration = default_exaggeration
        self.default_temperature = default_temperature
        self.default_repetition_penalty = default_repetition_penalty
        self.allow_roots = allow_roots
        self.lock = threading.Lock()
        self.reference_cache: OrderedDict[ReferenceKey, Any] = OrderedDict()
        self.reference_cache_size = reference_cache_size
        self.active_reference: ReferenceKey | None = None
        self.requests_served = 0
        self.last_error: str | None = None

    def within_allow_list(self, path: Path) -> bool:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return False
        return any(resolved == root or root in resolved.parents for root in self.allow_roots)

    def use_reference(self, reference: Path, exaggeration: float) -> bool:
        """Install conditionals for (reference, exaggeration); True if they were cached."""
        resolved = reference.expanduser().resolve()
        info = resolved.stat()
        key: ReferenceKey = (str(resolved), info.st_mtime_ns, info.st_size, exaggeration)
        conditionals = self.reference_cache.get(key)
        if conditionals is not None:
            self.reference_cache.move_to_end(key)
            self.model.conds = conditionals
            self.active_reference = key
            return True

        # One expensive pass per (clip, dial): audio decode, resample, speaker embedding,
        # conditioning tokens, and the emotion_adv scalar that carries exaggeration.
        self.model.prepare_conditionals(str(resolved), exaggeration=exaggeration)
        conditionals = self.model.conds
        if conditionals is None:
            raise RuntimeError("Chatterbox produced no voice conditionals")
        self.reference_cache[key] = conditionals
        self.reference_cache.move_to_end(key)
        self.active_reference = key
        while len(self.reference_cache) > self.reference_cache_size:
            self.reference_cache.popitem(last=False)
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "edvox-chatterbox/2"

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
        if self.path.rstrip("/") in ("/health", "/"):
            state: State = self.server.state  # type: ignore[attr-defined]
            with state.lock:
                active = state.active_reference
                self.send_json(200, {
                    "status": "ok",
                    "ready": state.model is not None,
                    "model": state.model_name,
                    "device": state.device,
                    "exaggeration": state.default_exaggeration,
                    "exaggeration_is_inert_on_this_model": state.model_name == "turbo",
                    "temperature": state.default_temperature,
                    "repetition_penalty": state.default_repetition_penalty,
                    "cached_references": len(state.reference_cache),
                    "active_reference": active[0] if active else None,
                    "active_exaggeration": active[3] if active else None,
                    "requests_served": state.requests_served,
                    "last_error": state.last_error,
                })
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/synthesize":
            self.send_json(404, {"error": "not found"})
            return
        state: State = self.server.state  # type: ignore[attr-defined]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
            text = str(request.get("text", "")).strip()
            if not text:
                raise ValueError("text is required")
            reference = str(request.get("reference", "")).strip()
            if not reference:
                raise ValueError("reference is required: Chatterbox needs a voice to clone")
            reference_path = Path(reference).expanduser()
            if not reference_path.is_file():
                raise FileNotFoundError(f"reference audio not found: {reference}")
            if not state.within_allow_list(reference_path):
                raise PermissionError("reference path is outside TTS_ALLOW_ROOTS")

            output = Path(str(request.get("output", ""))).expanduser()
            if not output.is_absolute():
                output = ROOT / output
            if not state.within_allow_list(output):
                raise PermissionError("output path is outside TTS_ALLOW_ROOTS")

            # Per-request dial, clamped to what has been listened to. NOTE: inert on the
            # Turbo checkpoint (no emotion_adv_fc); honoured by the standard/MTL weights.
            # The knobs that do move Turbo are temperature and repetition_penalty.
            exaggeration = request.get("exaggeration", state.default_exaggeration)
            exaggeration = min(max(float(exaggeration), 0.0), 2.0)
            temperature = request.get("temperature", state.default_temperature)
            temperature = min(max(float(temperature), 0.1), 1.5)
            repetition_penalty = request.get("repetition_penalty", state.default_repetition_penalty)

            started = time.monotonic()
            # Generation is not safely concurrent on one model/device; requests queue.
            with state.lock:
                cached = state.use_reference(reference_path, exaggeration)
                # No audio_prompt_path: use_reference() installed the conditionals already,
                # and passing the path would recompute them and discard the cache.
                wav = state.model.generate(
                    text,
                    temperature=temperature,
                    repetition_penalty=repetition_penalty,
                )
                seconds = float(wav.shape[-1]) / state.model.sr
                raw_peak = float(wav.abs().max())
                x = wav.squeeze(0).float().cpu()
                if raw_peak > PEAK_GUARD_THRESHOLD:
                    # norm_loudness() equalises loudness but does not bound amplitude, so
                    # full-scale excursions survive it. Scale rather than clamp the dial:
                    # measured peaks at exaggeration 1.5 were both 0.765 and 1.015, i.e. the
                    # dial does not map monotonically onto amplitude.
                    x = x * (PEAK_GUARD_TARGET / raw_peak)
                import torchaudio
                output.parent.mkdir(parents=True, exist_ok=True)
                # 16-bit PCM, like native/ttsd.cpp, so the browser and any downstream tool
                # get the same format from either backend.
                torchaudio.save(str(output), x.unsqueeze(0), state.model.sr,
                                encoding="PCM_S", bits_per_sample=16)
                state.requests_served += 1
                elapsed = time.monotonic() - started
            self.log_message("%s in %.2fs (audio %.2fs, temp %.2f, exagg %.1f, %s, peak %.3f)",
                             output.name, elapsed, seconds, temperature, exaggeration,
                             "cached" if cached else "prepared", raw_peak)
            self.send_json(200, {
                "status": "ok",
                "output": str(output),
                "audio_seconds": round(seconds, 3),
                "generation_seconds": round(elapsed, 3),
                "raw_peak": round(raw_peak, 3),
                "peak_guard_applied": raw_peak > PEAK_GUARD_THRESHOLD,
                "exaggeration": exaggeration,
                "temperature": temperature,
                "repetition_penalty": repetition_penalty,
                "dial_applies_to_model": state.model_name != "turbo",
                "reference_cached": cached,
                "sample_rate": state.model.sr,
            })
        except Exception as error:  # noqa: BLE001 - service errors belong to the client
            state.last_error = f"{type(error).__name__}: {error}"
            self.log_message("synthesis failed: %s", state.last_error)
            self.send_json(500, {"error": state.last_error})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", choices=("turbo", "nano"),
                        default=os.environ.get("CHATTERBOX_MODEL", "turbo"))
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--device", default=os.environ.get("CHATTERBOX_DEVICE", "cpu"))
    parser.add_argument("--host", default=os.environ.get("TTS_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TTS_PORT", "8190")))
    parser.add_argument("--exaggeration", type=float,
                        default=float(os.environ.get("CHATTERBOX_EXAGGERATION", "1.5")),
                        help="expressiveness dial baked into the voice conditionals; "
                             "inert on the turbo checkpoint")
    parser.add_argument("--temperature", type=float,
                        default=float(os.environ.get("CHATTERBOX_TEMPERATURE", "0.8")),
                        help="the lever that actually moves Turbo; higher is more varied "
                             "and more likely to mangle a word")
    parser.add_argument("--repetition-penalty", type=float,
                        default=float(os.environ.get("CHATTERBOX_REPETITION_PENALTY", "1.2")))
    parser.add_argument("--warmup-reference", default=os.environ.get("TTS_WARMUP_REFERENCE", ""),
                        help="prepare this reference clip at startup so turn one is warm")
    parser.add_argument("--reference-cache-size", type=int,
                        default=int(os.environ.get("TTS_REFERENCE_CACHE_SIZE", "4")))
    parser.add_argument("--allow-roots", default=os.environ.get("TTS_ALLOW_ROOTS", DEFAULT_ALLOW_ROOTS))
    args = parser.parse_args()

    model_dir = Path(args.model_dir or os.environ.get(
        "CHATTERBOX_MODEL_DIR", str(ROOT / "models" / f"chatterbox-{args.model}"))).expanduser()
    if not model_dir.is_dir():
        raise FileNotFoundError(f"Chatterbox model directory not found: {model_dir}")
    if args.reference_cache_size < 1:
        parser.error("--reference-cache-size must be at least 1")

    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    # chatterbox-tts 0.1.7's signature is from_local(ckpt_dir, device). There is no `nano`
    # flag: nano and turbo are distinguished by checkpoint directory, and 0.1.7's loader
    # cannot read nano's state dict at all (1 of 155 tensors shape-matches). Passing
    # nano= here is what broke the previous revision.
    device = args.device
    if device == "mps" and not torch.backends.mps.is_available():
        print("chatterbox-server: MPS unavailable, using CPU", file=sys.stderr, flush=True)
        device = "cpu"
    print(f"chatterbox-server: loading Chatterbox {args.model} on {device} "
          f"(exaggeration {args.exaggeration})...", file=sys.stderr, flush=True)
    started = time.monotonic()
    model = ChatterboxTurboTTS.from_local(str(model_dir), device)
    load_seconds = time.monotonic() - started
    print(f"chatterbox-server: ready in {load_seconds:.1f}s on {args.host}:{args.port} "
          f"(sr={model.sr})", file=sys.stderr, flush=True)

    allow_roots = parse_allow_roots(args.allow_roots)
    state = State(model, args.model, device, min(max(args.exaggeration, 0.0), 2.0),
                  args.reference_cache_size, allow_roots,
                  default_temperature=min(max(args.temperature, 0.1), 1.5),
                  default_repetition_penalty=max(args.repetition_penalty, 1.0))
    if args.warmup_reference:
        warm = Path(args.warmup_reference).expanduser()
        if warm.is_file() and state.within_allow_list(warm):
            t0 = time.monotonic()
            state.use_reference(warm, state.default_exaggeration)
            print(f"chatterbox-server: warmed reference {warm.name} in "
                  f"{time.monotonic() - t0:.1f}s", file=sys.stderr, flush=True)
        else:
            print(f"chatterbox-server: warmup reference unusable: {warm}", file=sys.stderr)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = state  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
