#!/usr/bin/env python3
"""One-shot local voice turn: record -> transcribe -> chat -> speak -> play."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def run(command: list[str], *, capture: bool = False) -> str:
    if capture:
        return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()
    subprocess.run(command, check=True)
    return ""


def record(output: Path, seconds: float, device: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Recording {seconds:g}s from AVFoundation device {device}...", file=sys.stderr)
    run([
        env("FFMPEG", "ffmpeg"), "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation", "-i", device, "-t", str(seconds),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(output),
    ])


def transcribe(audio: Path) -> str:
    output = run([
        str(ROOT / "bin" / "whisper-cli"),
        "-m", env("STT_MODEL", str(ROOT / "models" / "ggml-base.en.bin")),
        "-l", env("STT_LANG", "en"), "-nt", "-np", str(audio),
    ], capture=True)
    # whisper-cli writes diagnostics to stdout with some builds; retain only the
    # recognized text line(s), while allowing punctuation and multiline replies.
    ignored_prefixes = (
        "load_", "ggml_", "whisper_", "system_info:", "main:",
        "read_audio_data:", "operator():", "model size", "model params",
        "memory size", "total time", "timings:", "output",
    )
    lines = []
    for line in output.splitlines():
        line = line.strip()
        if line and not line.startswith(ignored_prefixes):
            lines.append(line)
    text = " ".join(lines).strip()
    if not text:
        raise RuntimeError("speech recognition returned no text")
    return text


def chat(text: str) -> str:
    body = {
        "model": env("CHAT_MODEL", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF:UD-Q4_K_XL"),
        "messages": [{"role": "user", "content": text}],
        "temperature": float(env("CHAT_TEMPERATURE", "0.7")),
        "max_tokens": int(env("CHAT_MAX_TOKENS", "256")),
        "stream": False,
        # The local Qwen chat server supports this and it avoids sending hidden
        # reasoning to the TTS model.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        env("CHAT_BASE_URL", "http://192.168.137.111:8088/v1").rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(env("CHAT_TIMEOUT", "300"))) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"chat server returned HTTP {error.code}: {detail}") from error
    try:
        answer = payload["choices"][0]["message"].get("content", "").strip()
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"unexpected chat response: {payload!r}") from error
    if not answer:
        raise RuntimeError("chat server returned an empty answer")
    return answer


def speak(text: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ROOT / "bin" / "llama-tts"),
        "-m", env("TTS_MODEL", str(ROOT / "models" / "Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf")),
        "-mm", env("TTS_MMPROJ", str(ROOT / "models" / "mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf")),
        "-p", text, "--tts-lang", env("TTS_LANG", "en"),
        "-n", env("TTS_FRAMES", "512"), "-o", str(output),
    ]
    speaker = env("TTS_SPEAKER_FILE", "")
    if speaker:
        command += ["--tts-speaker-file", speaker]
    print("Synthesizing response...", file=sys.stderr)
    run(command)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=float(env("RECORD_SECONDS", "5")))
    parser.add_argument("--device", default=env("AUDIO_DEVICE", ":1"))
    parser.add_argument("--audio", help="Use an existing audio file instead of recording")
    parser.add_argument("--text", help="Use text instead of recording (chat/TTS smoke test)")
    parser.add_argument("--no-play", action="store_true")
    parser.add_argument("--keep-audio", action="store_true")
    args = parser.parse_args()
    if args.audio and args.text:
        parser.error("--audio and --text cannot be used together")

    audio_dir = ROOT / "audio"
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    recorded = audio_dir / f"turn-{timestamp}.wav"
    response_audio = audio_dir / f"response-{timestamp}.wav"
    recorded_by_us = False

    try:
        if args.text:
            user_text = args.text
        else:
            if args.audio:
                recorded = Path(args.audio).expanduser().resolve()
                if not recorded.is_file():
                    raise FileNotFoundError(f"audio file not found: {recorded}")
            else:
                record(recorded, args.seconds, args.device)
                recorded_by_us = True
            print("Transcribing...", file=sys.stderr)
            user_text = transcribe(recorded)
        print(f"You: {user_text}")
        answer = chat(user_text)
        print(f"Assistant: {answer}")
        speak(answer, response_audio)
        if not args.no_play:
            run([env("AFPLAY", "afplay"), str(response_audio)])
        print(f"Audio: {response_audio}")
    finally:
        if recorded_by_us and not args.keep_audio and recorded.exists():
            recorded.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
