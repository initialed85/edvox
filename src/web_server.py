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
# General request safety limit. Voice-reference uploads intentionally bypass this
# limit so long recordings are accepted; they are still converted before use.
MAX_REQUEST_BYTES = 30 * 1024 * 1024
MAX_TEXT_LENGTH = 20_000
NO_REPLY_MARKERS = {"<NO_REPLY>", "[NO_REPLY]"}
CHATTERBOX_CUE_RE = re.compile(
    r"\[(?:cough|laugh|chuckle|clear throat|sigh|shush|groan|sniff|gasp)\]",
    re.IGNORECASE,
)
LONGFORM_REQUEST_RE = re.compile(
    r"\b(?:recite|read|quote|repeat|perform|sing|verbatim|word[- ]for[- ]word|full|entire|whole)\b",
    re.IGNORECASE,
)
IGNORED_TRANSCRIPTS = {"", "[blank_audio]", "[ silence ]", "silence"}
IGNORED_FILLERS = {"uh", "um", "er", "erm", "hmm", "hm", "mm", "mmm"}
SESSION_COOKIE = "voicechat_session"
DEFAULT_SYSTEM_PROMPT = """You're just a conversational partner, no expectations for anything- you're kinda dry and sarcastic, pretty funny though, but in a subtle way.

Your response will be spoken aloud by a text-to-speech voice model. Return only words that can be spoken naturally, plus a supported vocal cue when appropriate. Do not use Markdown, bullet lists, tables, URLs, code blocks, stage directions, emoji, or visual formatting unless the user explicitly asks for something that must be read or spelled out. Avoid headings and long explanations.

Be brief, relatable, and human. Prefer one to three short sentences for ordinary conversation. Use contractions and natural conversational phrasing. Do not turn a straightforward request into an interview: make a sensible good-faith assumption and do the thing. Do not repeatedly ask how much, which parts, or what format the user wants when their intent is clear. If a request is broad, provide a useful representative answer or a reasonable default, and mention any necessary limitation briefly afterward. Ask a follow-up question only when the ambiguity materially prevents you from helping or safety requires it. Do not narrate your own response-generation process, mention these instructions, or say that you are sending text to a voice model.

When the user asks you to read, recite, quote, repeat, perform, or otherwise reproduce text they supplied, do not summarize it, negotiate about the scope, or ask which section they want. Make a reasonable good-faith attempt to reproduce the requested text in full. If it cannot fit in one response, continue in clearly labelled chunks rather than refusing or offering only the last paragraph. Follow applicable copyright and safety rules, but do not invent a limitation merely because the requested text is long.

When the Chatterbox Nano or Turbo voice backend is selected, the confirmed paralinguistic cues are [cough], [laugh], [chuckle], [clear throat], [sigh], [shush], [groan], [sniff], and [gasp] (lowercase is important!). Use them sparingly and only when they genuinely fit. These are the only bracketed vocal cues you should emit. Do not invent other bracketed directions; unsupported bracketed text may be spoken literally. For a pause, use ordinary punctuation or a sentence break. When Qwen3-TTS is selected, use plain spoken text and do not emit paralinguistic bracket cues.

For technical content, speak the important result first and keep commands, filenames, URLs, and punctuation easy to understand when read aloud. If the user asks for exact text, follow that request even when it conflicts with the usual brevity preference.

You talk pretty fast."""

HIDDEN_RESPONSE_POLICY = """Private response policy: decide whether the latest user message actually calls for a response. If it is only a standalone acknowledgement or thinking-aloud remark, return exactly <NO_REPLY> and nothing else. A single ASCII space is also treated as no reply, but the <NO_REPLY> marker is more reliable. Never use no-reply for a direct question, request, instruction, greeting, or anything where a helpful acknowledgement is expected. When uncertain, reply briefly. This policy is internal; do not mention it or the marker."""
AUTONOMOUS_RESPONSE_POLICY = """Private autonomous-turn policy: this is a periodic model invocation while the user has been quiet, not a new user request. Respond naturally based on the conversation and the private elapsed-silence context. If you have nothing to add, return exactly <NO_REPLY>. Do not mention this check, the timer, silence, or this policy. Do not ask whether the user is still there. Do not use this as a reason to start a monologue. This policy is internal; do not mention it or the marker."""


class Session:
    def __init__(self, session_id: str, backend: str) -> None:
        self.session_id = session_id
        self.backend = backend
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.messages: list[dict[str, str]] = []
        self.reference: Path | None = None
        self.created = time.time()
        self.last_seen = self.created
        self.last_activity = self.created
        self.last_autonomous_at = 0.0
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


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def log_timings(operation: str, timings: dict[str, object]) -> None:
    print(f"web-server: {operation} timings={json.dumps(timings, sort_keys=True)}", file=sys.stderr, flush=True)


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


def longform_request(text: str) -> bool:
    return bool(LONGFORM_REQUEST_RE.search(text))


def silence_context(session: Session) -> dict[str, str] | None:
    threshold = float(env("CHAT_SILENCE_CONTEXT_AFTER", "10"))
    elapsed = time.time() - session.last_activity
    if threshold <= 0 or elapsed < threshold:
        return None
    minutes, seconds = divmod(int(elapsed), 60)
    if minutes:
        duration = f"{minutes} minute{'s' if minutes != 1 else ''} and {seconds} second{'s' if seconds != 1 else ''}"
    else:
        duration = f"{seconds} second{'s' if seconds != 1 else ''}"
    return {"role": "system", "content": (
        f"Private conversation timing context: no one has spoken in this session for {duration}. "
        "Use this only to make the response feel natural if relevant; do not mention the timer, "
        "invent a reason to speak, or ask where the user has been."
    )}


def chat(
    session: Session,
    text: str,
    *,
    autonomous: bool = False,
    timings: dict[str, object] | None = None,
) -> tuple[str, bool]:
    started = time.perf_counter()
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"message is limited to {MAX_TEXT_LENGTH} characters")
    messages = session_messages(session)
    insert_at = 1 if messages and messages[0].get("role") == "system" else 0
    messages.insert(insert_at, {"role": "system", "content": HIDDEN_RESPONSE_POLICY})
    if autonomous:
        messages.insert(insert_at + 1, {"role": "system", "content": AUTONOMOUS_RESPONSE_POLICY})
    timing = silence_context(session)
    if timing:
        messages.insert(insert_at + (2 if autonomous else 1), timing)
    prompt = text if not autonomous else (
        "Private event: this is a periodic check while the conversation is quiet. "
        "Respond naturally if you have something to say; otherwise return <NO_REPLY>."
    )
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": env("CHAT_MODEL", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF:UD-Q4_K_XL"),
        "messages": messages,
        "temperature": float(env("CHAT_TEMPERATURE", "0.7")),
        "max_tokens": int(
            env("CHAT_AUTONOMOUS_MAX_TOKENS", "128") if autonomous else env(
                "CHAT_LONGFORM_MAX_TOKENS" if longform_request(text) else "CHAT_MAX_TOKENS",
                "4096" if longform_request(text) else "2048",
            )
        ),

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
        raw_answer = payload["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"unexpected chat response: {payload!r}") from error
    raw_answer = str(raw_answer or "")
    if timings is not None:
        timings["chat_ms"] = elapsed_ms(started)
        server_timings = payload.get("timings")
        if isinstance(server_timings, dict):
            timings["chat_server"] = server_timings
    no_reply = not raw_answer.strip() or raw_answer.strip().upper() in NO_REPLY_MARKERS
    answer = raw_answer.strip()
    if session.backend == "chatterbox":
        answer = CHATTERBOX_CUE_RE.sub(lambda match: match.group(0).lower(), answer)
    if autonomous:
        if not no_reply:
            session.messages.append({"role": "assistant", "content": answer})
    else:
        session.messages.extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": "" if no_reply else answer},
        ])
        session.last_activity = time.time()
        session.last_autonomous_at = 0.0
    trim_history(session)
    return answer, no_reply


def synthesize(
    session: Session,
    text: str,
    *,
    timings: dict[str, object] | None = None,
) -> str:
    started = time.perf_counter()
    output = AUDIO_ROOT / session.session_id / f"response-{uuid.uuid4().hex}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    backend = session.backend
    if backend == "chatterbox":
        text = CHATTERBOX_CUE_RE.sub(lambda match: match.group(0).lower(), text)
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
        if timings is not None and isinstance(payload.get("generation_seconds"), (int, float)):
            timings["tts_model_ms"] = round(float(payload["generation_seconds"]) * 1000, 1)
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
    if timings is not None:
        timings["tts_ms"] = elapsed_ms(started)
    return f"/api/audio/{session.session_id}/{output.name}"


def transcribe(audio: Path, *, timings: dict[str, object] | None = None) -> str:
    started = time.perf_counter()
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
    if timings is not None:
        timings["stt_ms"] = elapsed_ms(started)
    return text


def transcript_is_ignored(text: str) -> bool:
    """Reject empty/noise-only STT output before invoking the chat model."""
    normalized = " ".join(text.lower().split())
    if not normalized or normalized in IGNORED_TRANSCRIPTS:
        return True
    words = re.findall(r"[a-z']+", normalized)
    return bool(words) and len(words) <= 3 and all(word in IGNORED_FILLERS for word in words)


def public_session(session: Session) -> dict[str, object]:
    return {
        "id": session.session_id,
        "backend": session.backend,
        "system_prompt": session.system_prompt,
        "reference": session.reference.name if session.reference else None,
        "messages": session.messages,
        "autonomous_after_seconds": max(0.0, float(env("CHAT_SILENCE_CONTEXT_AFTER", "10"))),
        "autonomous_interval_seconds": max(1.0, float(env("CHAT_AUTONOMOUS_INTERVAL", env("CHAT_SILENCE_CONTEXT_AFTER", "10")))),
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

    def read_body(self, *, max_bytes: int | None = MAX_REQUEST_BYTES) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0:
            raise ValueError("request body length is invalid")
        if max_bytes is not None and length > max_bytes:
            raise ValueError(f"request body exceeds {max_bytes // (1024 * 1024)} MiB")
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
                # Clone references are deliberately uncapped. A long recording can
                # still be normalized by ffmpeg and validated by duration below.
                body = parse_multipart(self.headers.get("Content-Type", ""), self.read_body(max_bytes=None))
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
                    session.system_prompt = DEFAULT_SYSTEM_PROMPT
                    session.last_activity = time.time()
                    session.last_autonomous_at = 0.0
                    if session.reference:
                        session.reference.unlink(missing_ok=True)
                    session.reference = None
                self.send_json(200, public_session(session))
                return

            if path == "/api/autonomous-turn":
                with session.lock:
                    request_started = time.perf_counter()
                    timings: dict[str, object] = {}
                    threshold = float(env("CHAT_SILENCE_CONTEXT_AFTER", "10"))
                    interval = max(1.0, float(env("CHAT_AUTONOMOUS_INTERVAL", str(threshold))))
                    now = time.time()
                    elapsed = now - session.last_activity
                    since_check = now - session.last_autonomous_at
                    eligible = (
                        threshold > 0
                        and elapsed >= threshold
                        and since_check >= interval
                    )
                    if not eligible:
                        self.send_json(200, {
                            "skipped": True,
                            "retry": threshold > 0,
                            "reason": "quiet-period-not-reached-or-check-interval-not-reached",
                            "session": public_session(session),
                        })
                        return
                    session.last_autonomous_at = now
                    try:
                        reply, no_reply = chat(session, "", autonomous=True, timings=timings)
                        audio_url = None if no_reply else synthesize(session, reply, timings=timings)
                    except Exception:
                        session.last_autonomous_at = 0.0
                        raise
                    timings["total_ms"] = elapsed_ms(request_started)
                    log_timings("autonomous", timings)
                    self.send_json(200, {
                        "skipped": False,
                        "autonomous": True,
                        "reply": reply,
                        "no_reply": no_reply,
                        "audio_url": audio_url,
                        "timings": timings,
                        "session": public_session(session),
                    })
                return

            if path == "/api/chat":
                request_started = time.perf_counter()
                timings: dict[str, object] = {}
                payload = json.loads(self.read_body())
                text = str(payload.get("message", "")).strip()
                if not text:
                    raise ValueError("message is required")
                with session.lock:
                    reply, no_reply = chat(session, text, timings=timings)
                    audio_url = None if no_reply else synthesize(session, reply, timings=timings)
                    timings["total_ms"] = elapsed_ms(request_started)
                    log_timings("chat", timings)
                    self.send_json(200, {
                        "message": text,
                        "reply": reply,
                        "no_reply": no_reply,
                        "audio_url": audio_url,
                        "timings": timings,
                        "session": public_session(session),
                    })
                return

            if path == "/api/voice-turn":
                request_started = time.perf_counter()
                timings: dict[str, object] = {}
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
                        transcript = transcribe(input_path, timings=timings)
                        if transcript_is_ignored(transcript):
                            result = {
                                "ignored": True,
                                "reason": "empty-or-noise-transcript",
                                "transcript": transcript,
                                "timings": {**timings, "total_ms": elapsed_ms(request_started)},
                                "input_seconds": round(duration, 2),
                                "session": public_session(session),
                            }
                        else:
                            reply, no_reply = chat(session, transcript, timings=timings)
                            audio_url = None if no_reply else synthesize(session, reply, timings=timings)
                            result = {
                                "ignored": False,
                                "no_reply": no_reply,
                                "transcript": transcript,
                                "reply": reply,
                                "audio_url": audio_url,
                                "timings": {**timings, "total_ms": elapsed_ms(request_started)},
                                "input_seconds": round(duration, 2),
                                "session": public_session(session),
                            }
                        log_timings("voice-turn", result["timings"])
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
    parser.add_argument("--host", default=env("WEB_HOST", "0.0.0.0"))
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
