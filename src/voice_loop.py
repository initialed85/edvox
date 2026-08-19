#!/usr/bin/env python3
"""Local voice turns with optional in-memory conversation history."""
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
    if env("STT_BACKEND", "server").lower() in {"server", "http", "whisper-server"}:
        command = [sys.executable, str(ROOT / "src" / "whisper_client.py"), str(audio)]
    else:
        command = [
            str(ROOT / "bin" / "whisper-cli"),
            "-m", env("STT_MODEL", str(ROOT / "models" / "ggml-base.en.bin")),
            "-l", env("STT_LANG", "en"), "-nt", "-np", str(audio),
        ]
    output = run(command, capture=True)
    if env("STT_BACKEND", "server").lower() in {"server", "http", "whisper-server"}:
        text = output.strip()
    else:
        ignored_prefixes = (
            "load_", "ggml_", "whisper_", "system_info:", "main:",
            "read_audio_data:", "operator():", "model size", "model params",
            "memory size", "total time", "timings:", "output",
        )
        text = " ".join(
            line.strip() for line in output.splitlines()
            if line.strip() and not line.strip().startswith(ignored_prefixes)
        ).strip()
    if not text:
        raise RuntimeError("speech recognition returned no text")
    return text


def chat(text: str, messages: list[dict[str, str]]) -> str:
    request_messages = [*messages, {"role": "user", "content": text}]
    body = {
        "model": env("CHAT_MODEL", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF:UD-Q4_K_XL"),
        "messages": request_messages,
        "temperature": float(env("CHAT_TEMPERATURE", "0.7")),
        "max_tokens": int(env("CHAT_MAX_TOKENS", "256")),
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        env("CHAT_BASE_URL", "http://127.0.0.1:8088/v1").rstrip("/") + "/chat/completions",
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


def trim_messages(messages: list[dict[str, str]], turns: int) -> list[dict[str, str]]:
    """Keep the system message and at most ``turns`` user/assistant pairs."""
    system = next((message for message in messages if message["role"] == "system"), None)
    dialogue = [message for message in messages if message["role"] != "system"]
    dialogue = dialogue[-(turns * 2):] if turns else []
    return ([system] if system else []) + dialogue


def load_system_prompt(args: argparse.Namespace) -> str:
    if args.system_prompt is not None and args.system_prompt_file:
        raise ValueError("--system-prompt and --system-prompt-file cannot be used together")
    prompt_file = args.system_prompt_file or env("CHAT_SYSTEM_PROMPT_FILE", "")
    if prompt_file:
        path = Path(prompt_file).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"system prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()
    if args.system_prompt is not None:
        return args.system_prompt.strip()
    return env("CHAT_SYSTEM_PROMPT", "").strip()


def speak_qwen(text: str, output: Path) -> None:
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
    print("Synthesizing response with Qwen3-TTS...", file=sys.stderr)
    run(command)


def speak_chatterbox(text: str, output: Path) -> None:
    speaker = env("TTS_SPEAKER_FILE", "")
    url = env("TTS_URL", "http://127.0.0.1:8190").rstrip("/") + "/synthesize"
    body = json.dumps({"text": text, "output": str(output), "reference": speaker}).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    print("Synthesizing response with resident Chatterbox...", file=sys.stderr)
    try:
        with urllib.request.urlopen(request, timeout=float(env("TTS_TIMEOUT", "300"))) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"TTS server returned HTTP {error.code}: {error.read().decode(errors='replace')}") from error
    if not payload.get("output"):
        raise RuntimeError(f"TTS server returned an invalid response: {payload!r}")


def speak(text: str, output: Path) -> None:
    backend = env("TTS_BACKEND", "qwen").lower()
    if backend in {"chatterbox", "chatterbox-nano", "nano", "turbo"}:
        model = env("CHATTERBOX_MODEL", "turbo" if backend == "turbo" else "nano")
        # Keep the backend alias useful without mutating the caller's environment.
        os.environ["CHATTERBOX_MODEL"] = model
        speak_chatterbox(text, output)
    elif backend in {"qwen", "qwen3", "qwen3-tts"}:
        speak_qwen(text, output)
    else:
        raise ValueError(f"unsupported TTS_BACKEND: {backend}")


def response_paths(turn_number: int) -> tuple[Path, Path]:
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}-t{turn_number}"
    return ROOT / "audio" / f"turn-{stamp}.wav", ROOT / "audio" / f"response-{stamp}.wav"


def execute_turn(
    args: argparse.Namespace,
    messages: list[dict[str, str]],
    turn_number: int,
    *,
    forced_text: str | None = None,
    audio_path: Path | None = None,
) -> None:
    recorded, response_audio = response_paths(turn_number)
    recorded_by_us = False
    try:
        if forced_text is not None:
            user_text = forced_text.strip()
            if not user_text:
                raise RuntimeError("text input was empty")
        else:
            if audio_path is None:
                record(recorded, args.seconds, args.device)
                audio_path = recorded
                recorded_by_us = True
            elif not audio_path.is_file():
                raise FileNotFoundError(f"audio file not found: {audio_path}")
            print("Transcribing...", file=sys.stderr)
            user_text = transcribe(audio_path)
        print(f"You: {user_text}")

        answer = chat(user_text, messages)
        messages.extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": answer},
        ])
        messages[:] = trim_messages(messages, args.history_turns)
        print(f"Assistant: {answer}")
        speak(answer, response_audio)
        if not args.no_play:
            run([env("AFPLAY", "afplay"), str(response_audio)])
        print(f"Audio: {response_audio}")
    finally:
        if recorded_by_us and not args.keep_audio and recorded.exists():
            recorded.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=float(env("RECORD_SECONDS", "5")))
    parser.add_argument("--device", default=env("AUDIO_DEVICE", ":1"))
    parser.add_argument("--audio", help="Use an existing audio file instead of recording")
    parser.add_argument("--text", help="Use text instead of recording (chat/TTS smoke test)")
    parser.add_argument("--interactive", action="store_true", help="Keep taking turns until Ctrl-C/EOF")
    parser.add_argument("--system-prompt", help="System prompt for this process only")
    parser.add_argument("--system-prompt-file", help="Read the system prompt from a file for this process only")
    parser.add_argument(
        "--history-turns", type=int,
        default=int(env("CHAT_HISTORY_TURNS", "8")),
        help="Previous user/assistant turns to retain in memory (default: 8)",
    )
    parser.add_argument("--no-play", action="store_true")
    parser.add_argument("--keep-audio", action="store_true")
    args = parser.parse_args()
    if args.audio and (args.text is not None or args.interactive):
        parser.error("--audio cannot be combined with --text or --interactive")
    if args.history_turns < 0:
        parser.error("--history-turns must be zero or greater")

    system_prompt = load_system_prompt(args)
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
        print("Using a process-local system prompt.", file=sys.stderr)
    if args.history_turns == 0:
        print("Conversation history disabled.", file=sys.stderr)
    elif args.interactive:
        print(f"Conversation history: last {args.history_turns} turns (memory only).", file=sys.stderr)

    if not args.interactive:
        if args.audio:
            execute_turn(
                args, messages, 1,
                audio_path=Path(args.audio).expanduser().resolve(),
            )
        else:
            execute_turn(args, messages, 1, forced_text=args.text)
        return 0

    turn_number = 1
    pending_text = args.text
    try:
        while True:
            if args.text is not None or (not sys.stdin.isatty() and pending_text is None):
                if pending_text is not None:
                    forced_text = pending_text
                    pending_text = None
                else:
                    try:
                        forced_text = input("You (text): ")
                    except EOFError:
                        break
            else:
                forced_text = None
            execute_turn(args, messages, turn_number, forced_text=forced_text)
            turn_number += 1
    except KeyboardInterrupt:
        print("\nVoice session ended.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
