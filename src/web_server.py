#!/usr/bin/env python3
"""Small local web frontend for the voice-chat services."""
from __future__ import annotations

import argparse
import email.message
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import wave
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
VOICE_ROOT = ROOT / "voices" / "web"
AUDIO_ROOT = ROOT / "audio" / "web"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_TEXT_LENGTH = 20_000
SESSION_COOKIE = "voicechat_session"


class Session:
    def __init__(self, session_id: str, backend: str) -> None:
        self.session_id = session_id
        self.backend = backend
        self.system_prompt = ""
        self.messages: list[dict[str, str]] = []
        self.reference: Path | None = None
        self.created = time.time()
        self.last_seen = self.created
        self.lock = threading.Lock()


SESSIONS: dict[str, Session] = {}
SESSIONS_LOCK = threading.Lock()


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def create_session() -> Session:
    session = Session(uuid.uuid4().hex, env("WEB_DEFAULT_TTS_BACKEND", "chatterbox"))
    with SESSIONS_LOCK:
        SESSIONS[session.session_id] = session
    return session


def get_session(session_id: str | None) -> tuple[Session, bool]:
    if session_id:
        with SESSIONS_LOCK:
            session = SESSIONS.get(session_id)
        if session is not None:
            session.last_seen = time.time()
            return session, False
    return create_session(), True


def cleanup_sessions() -> None:
    cutoff = time.time() - float(env("WEB_SESSION_TTL", "86400"))
    with SESSIONS_LOCK:
        stale = [sid for sid, session in SESSIONS.items() if session.last_seen < cutoff]
        for sid in stale:
            SESSIONS.pop(sid, None)
    for sid in stale:
        for root in (VOICE_ROOT / sid, AUDIO_ROOT / sid):
            if root.is_dir():
                for path in sorted(root.rglob("*"), reverse=True):
                    if path.is_file() or path.is_symlink():
                        path.unlink(missing_ok=True)
                    elif path.is_dir():
                        path.rmdir()
                root.rmdir()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def parse_cookie(header: str) -> str | None:
    cookies = SimpleCookie()
    cookies.load(header or "")
    morsel = cookies.get(SESSION_COOKIE)
    return morsel.value if morsel else None


def parse_multipart(content_type: str, body: bytes) -> dict[str, tuple[str, str, bytes]]:
    """Parse the small multipart forms emitted by the browser."""
    match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
    if not match:
        raise ValueError("multipart boundary is missing")
    boundary = (match.group(1) or match.group(2)).encode()
    result: dict[str, tuple[str, str, bytes]] = {}
    marker = b"--" + boundary
    for chunk in body.split(marker)[1:]:
        if chunk.startswith(b"--"):
            break
        chunk = chunk.strip(b"\r\n")
        if not chunk:
            continue
        header_bytes, separator, payload = chunk.partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("malformed multipart form")
        headers = email.message_from_bytes(header_bytes)
        disposition = headers.get("Content-Disposition", "")
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        filename = filename_match.group(1) if filename_match else ""
        result[name_match.group(1)] = (filename, headers.get_content_type(), payload)
    return result


def safe_filename(filename: str, default: str) -> str:
    suffix = Path(filename).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else default


def run_checked(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def convert_audio(data: bytes, suffix: str, destination: Path) -> float:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = destination.parent / f".{destination.stem}.upload-{uuid.uuid4().hex}{suffix or '.audio'}"
    source.write_bytes(data)
    try:
        run_checked([
            env("FFMPEG", "ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            "-y", str(destination),
        ], timeout=60)
    except FileNotFoundError as error:
        raise RuntimeError("ffmpeg is required for browser audio uploads") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors="replace").strip()
        raise ValueError(f"could not decode audio upload: {detail[-500:]}") from error
    finally:
        source.unlink(missing_ok=True)
    try:
        with wave.open(str(destination), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
    except (wave.Error, OSError) as error:
        destination.unlink(missing_ok=True)
        raise ValueError("converted upload is not a valid WAV file") from error
    return duration


def service_json(url: str, timeout: float = 1.5) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            value = json.load(response)
        return value if isinstance(value, dict) else {"status": "invalid"}
    except Exception as error:  # noqa: BLE001 - health is diagnostic
        return {"status": "offline", "error": str(error)}


def services_status() -> dict[str, object]:
    chat_host = env("CHAT_HOST", "127.0.0.1")
    chat_port = env("CHAT_PORT", "8088")
    stt_url = env("STT_URL", "http://127.0.0.1:8178").rstrip("/")
    tts_url = env("TTS_URL", "http://127.0.0.1:8190").rstrip("/")
    return {
        "chat": service_json(f"http://{chat_host}:{chat_port}/health"),
        "stt": service_json(f"{stt_url}/health"),
        "tts": service_json(f"{tts_url}/health"),
    }


def session_messages(session: Session) -> list[dict[str, str]]:
    messages = []
    if session.system_prompt:
        messages.append({"role": "system", "content": session.system_prompt})
    messages.extend(session.messages)
    return messages


def trim_history(session: Session) -> None:
    turns = max(0, int(env("CHAT_HISTORY_TURNS", "8")))
    session.messages = session.messages[-(turns * 2):] if turns else []


def chat(session: Session, text: str) -> str:
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"message is limited to {MAX_TEXT_LENGTH} characters")
    body = {
        "model": env("CHAT_MODEL", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF:UD-Q4_K_XL"),
        "messages": [*session_messages(session), {"role": "user", "content": text}],
        "temperature": float(env("CHAT_TEMPERATURE", "0.7")),
        "max_tokens": int(env("CHAT_MAX_TOKENS", "256")),
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        env("CHAT_BASE_URL", "http://127.0.0.1:8088/v1").rstrip("/") + "/chat/completions",
        data=json_bytes(body),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(env("CHAT_TIMEOUT", "300"))) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"chat server returned HTTP {error.code}: {detail[-1000:]}") from error
    try:
        answer = payload["choices"][0]["message"].get("content", "").strip()
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"unexpected chat response: {payload!r}") from error
    if not answer:
        raise RuntimeError("chat server returned an empty answer")
    session.messages.extend([
        {"role": "user", "content": text},
        {"role": "assistant", "content": answer},
    ])
    trim_history(session)
    return answer


def synthesize(session: Session, text: str) -> str:
    output = AUDIO_ROOT / session.session_id / f"response-{uuid.uuid4().hex}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    backend = session.backend
    if backend == "chatterbox":
        url = env("TTS_URL", "http://127.0.0.1:8190").rstrip("/") + "/synthesize"
        body = {
            "text": text,
            "output": str(output),
            "reference": str(session.reference) if session.reference else "",
        }
        request = urllib.request.Request(
            url, data=json_bytes(body), headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=float(env("TTS_TIMEOUT", "300"))) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"Chatterbox returned HTTP {error.code}: {detail[-1000:]}") from error
        if not payload.get("output"):
            raise RuntimeError(f"Chatterbox returned an invalid response: {payload!r}")
    elif backend == "qwen":
        command = [
            str(ROOT / "bin" / "llama-tts"),
            "-m", env("TTS_MODEL", str(ROOT / "models" / "Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf")),
            "-mm", env("TTS_MMPROJ", str(ROOT / "models" / "mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf")),
            "-p", text, "--tts-lang", env("TTS_LANG", "en"),
            "-n", env("TTS_FRAMES", "512"), "-o", str(output),
        ]
        if session.reference:
            command += ["--tts-speaker-file", str(session.reference)]
        try:
            run_checked(command, timeout=float(env("TTS_TIMEOUT", "300")))
        except FileNotFoundError as error:
            raise RuntimeError(f"Qwen TTS executable not found: {command[0]}") from error
        except subprocess.CalledProcessError as error:
            detail = error.stderr.decode(errors="replace")
            raise RuntimeError(f"Qwen TTS failed: {detail[-1000:]}") from error
    else:
        raise ValueError(f"unsupported TTS backend: {backend}")
    if not output.is_file():
        raise RuntimeError("TTS completed without producing an audio file")
    return f"/api/audio/{session.session_id}/{output.name}"


def transcribe(audio: Path) -> str:
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "src" / "whisper_client.py"), str(audio)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=float(env("STT_TIMEOUT", "120")),
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        raise RuntimeError(f"speech recognition failed: {detail[-1000:]}") from error
    text = result.stdout.strip()
    if not text:
        raise RuntimeError("speech recognition returned no text")
    return text


def public_session(session: Session) -> dict[str, object]:
    return {
        "id": session.session_id,
        "backend": session.backend,
        "system_prompt": session.system_prompt,
        "reference": session.reference.name if session.reference else None,
        "messages": session.messages,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "voice-chat-web/0.1"

    @property
    def app(self) -> "WebServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"web-server: {fmt % args}", file=sys.stderr, flush=True)

    def session(self) -> Session:
        session, created = get_session(parse_cookie(self.headers.get("Cookie", "")))
        self.current_session = session
        self.new_session_cookie = created
        return session

    def send_bytes(self, status: int, data: bytes, content_type: str, *, cookie: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie and getattr(self, "new_session_cookie", False):
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={self.current_session.session_id}; Path=/; SameSite=Strict")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status: int, value: object) -> None:
        self.send_bytes(status, json_bytes(value), "application/json; charset=utf-8")

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json(status, {"error": message})

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError(f"request body exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB")
        return self.rfile.read(length)

    def do_GET(self) -> None:
        cleanup_sessions()
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/session":
            session = self.session()
            with session.lock:
                self.send_json(200, public_session(session))
            return
        if path == "/api/health":
            self.session()
            self.send_json(200, {"services": services_status()})
            return
        match = re.fullmatch(r"/api/audio/([a-f0-9]{32})/([A-Za-z0-9._-]+)", path)
        if match:
            self.session()
            session_id, filename = match.groups()
            with SESSIONS_LOCK:
                exists = session_id in SESSIONS
            audio = AUDIO_ROOT / session_id / filename
            if not exists or audio.parent != AUDIO_ROOT / session_id or not audio.is_file():
                self.send_error_json(404, "audio not found")
                return
            self.send_bytes(200, audio.read_bytes(), "audio/wav")
            return
        if path == "/" or path == "/index.html":
            self.session()
            self.send_bytes(200, (WEB_ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path in {"/app.js", "/style.css"}:
            self.session()
            file = WEB_ROOT / path.lstrip("/")
            if not file.is_file():
                self.send_error_json(404, "not found")
                return
            content_type = "application/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            self.send_bytes(200, file.read_bytes(), content_type)
            return
        self.send_error_json(404, "not found")

    def do_POST(self) -> None:
        cleanup_sessions()
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        session = self.session()
        try:
            if path == "/api/session/settings":
                payload = json.loads(self.read_body())
                backend = payload.get("backend", session.backend)
                if backend not in {"qwen", "chatterbox"}:
                    raise ValueError("backend must be qwen or chatterbox")
                prompt = str(payload.get("system_prompt", session.system_prompt)).strip()
                if len(prompt) > MAX_TEXT_LENGTH:
                    raise ValueError(f"system prompt is limited to {MAX_TEXT_LENGTH} characters")
                with session.lock:
                    session.backend = backend
                    session.system_prompt = prompt
                self.send_json(200, public_session(session))
                return

            if path == "/api/session/voice":
                body = parse_multipart(self.headers.get("Content-Type", ""), self.read_body())
                if "voice" not in body:
                    raise ValueError("voice file is required")
                filename, _content_type, data = body["voice"]
                if not data:
                    raise ValueError("voice upload is empty")
                session_dir = VOICE_ROOT / session.session_id
                destination = session_dir / "reference.wav"
                duration = convert_audio(data, safe_filename(filename, ".audio"), destination)
                if duration <= 5.0:
                    destination.unlink(missing_ok=True)
                    raise ValueError(f"voice reference must be longer than five seconds (received {duration:.1f}s)")
                with session.lock:
                    old = session.reference
                    session.reference = destination
                if old and old != destination:
                    old.unlink(missing_ok=True)
                self.send_json(200, {"reference": destination.name, "duration_seconds": round(duration, 2)})
                return

            if path == "/api/session/clear-voice":
                with session.lock:
                    if session.reference:
                        session.reference.unlink(missing_ok=True)
                    session.reference = None
                self.send_json(200, public_session(session))
                return

            if path == "/api/session/reset":
                with session.lock:
                    session.messages.clear()
                    session.system_prompt = ""
                    if session.reference:
                        session.reference.unlink(missing_ok=True)
                    session.reference = None
                self.send_json(200, public_session(session))
                return

            if path == "/api/chat":
                payload = json.loads(self.read_body())
                text = str(payload.get("message", "")).strip()
                if not text:
                    raise ValueError("message is required")
                with session.lock:
                    reply = chat(session, text)
                    audio_url = synthesize(session, reply)
                    self.send_json(200, {"message": text, "reply": reply, "audio_url": audio_url, "session": public_session(session)})
                return

            if path == "/api/voice-turn":
                body = parse_multipart(self.headers.get("Content-Type", ""), self.read_body())
                if "audio" not in body:
                    raise ValueError("audio file is required")
                filename, _content_type, data = body["audio"]
                if not data:
                    raise ValueError("audio upload is empty")
                input_path = AUDIO_ROOT / session.session_id / f"input-{uuid.uuid4().hex}.wav"
                duration = convert_audio(data, safe_filename(filename, ".audio"), input_path)
                try:
                    with session.lock:
                        transcript = transcribe(input_path)
                        reply = chat(session, transcript)
                        audio_url = synthesize(session, reply)
                        result = {
                            "transcript": transcript,
                            "reply": reply,
                            "audio_url": audio_url,
                            "input_seconds": round(duration, 2),
                            "session": public_session(session),
                        }
                    self.send_json(200, result)
                finally:
                    input_path.unlink(missing_ok=True)
                return
            self.send_error_json(404, "not found")
        except json.JSONDecodeError as error:
            self.send_error_json(400, f"invalid JSON: {error}")
        except (ValueError, FileNotFoundError) as error:
            self.send_error_json(400, str(error))
        except subprocess.TimeoutExpired:
            self.send_error_json(504, "backend operation timed out")
        except Exception as error:  # noqa: BLE001 - local POC returns useful errors
            self.log_message("request failed: %s", error)
            self.send_error_json(500, str(error))


class WebServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=env("WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(env("WEB_PORT", "8200")))
    args = parser.parse_args()
    if not WEB_ROOT.is_dir():
        raise FileNotFoundError(f"web frontend not found: {WEB_ROOT}")
    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"voice-chat web UI listening on http://{args.host}:{args.port}", file=sys.stderr, flush=True)
    server = WebServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
