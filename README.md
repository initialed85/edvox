# voice-chat

A local voice interface for the llama.cpp chat server on the Apple Silicon
host. The components are deliberately replaceable:

```text
microphone -> Whisper STT -> llama-server chat -> TTS backend -> speakers
```

## Web frontend

The primary browser frontend is a deliberately minimal conversation surface at
`/`. It starts VOX automatically when the page opens, keeps the fixed
`DEFAULT_PERSONA`, and provides no persona, backend, voice-reference, text-chat,
or mode controls. The single status orb indicates listening, processing,
speaking, and microphone errors; it pulses while listening and reverberates
while audio is playing. Tap it only to retry microphone permission after a
startup failure.

The previous full-featured browser frontend is retained at `/admin/`. It
provides session-local backend selection, persona editing, voice-reference
upload, text chat, generated audio playback, push-to-talk, VOX controls,
and autonomous-check settings. The `/admin/` route is an operational surface,
not an authentication boundary; add authentication before exposing it beyond
the trusted network.

Both surfaces use ordinary `getUserMedia` permission; no macOS Accessibility
permission is needed. The default web binding is `0.0.0.0`, so the UI can be
opened from another device on the LAN. The backend services remain on loopback;
only the browser-facing web UI is exposed. Do not forward this port to the
public internet without authentication and HTTPS.

### Minimal conversation VOX mode

After microphone permission is granted, the primary frontend analyzes the
microphone signal locally and uploads only completed speech segments. It uses the local browser audio constraints plus the in-browser Silero VAD v5
model. Silero uses speech probability, separate start/end thresholds, 800 ms
pre-speech padding, a 2-second redemption period for natural pauses, and a
350 ms minimum speech duration. Speech that resumes during the redemption
period remains part of the same 16 kHz PCM segment rather than being submitted
as a second turn. Empty, silence-only, and short filler-only Whisper transcripts
are still discarded by the web server before the chat model or TTS is called.

VOX is half-duplex: it releases microphone capture while the assistant's reply
plays, then arms itself again. The browser also makes recurring autonomous
quiet-period checks, including on a fresh session, and the model may return
`<NO_REPLY>`. Checks pause while recording, processing, or speaking. Screen
Wake Lock remains best-effort while the HTTPS page is visible.

The application adds a server-owned runtime contract and the fixed
`DEFAULT_PERSONA` to conversation-mode requests. The persona is not exposed by
the primary UI and cannot be changed there. Chatterbox Nano and Turbo officially document these paralinguistic tags: `[cough]`, `[laugh]`,
`[chuckle]`, `[clear throat]`, `[sigh]`, `[shush]`, `[groan]`, `[sniff]`, and
`[gasp]`. The web server normalizes these supported cues to lowercase before
storing and sending Chatterbox responses. Do not use arbitrary bracketed
directions such as `[slight pause]`; unsupported text may be spoken literally.
Use ordinary punctuation for pauses.

Start the frontend and its resident services:

```bash
./scripts/start-chat-background.sh
./scripts/start-whisper-background.sh
./scripts/start-tts-background.sh
./scripts/start-web-background.sh
```

Then open <http://127.0.0.1:8200>. For a local-only binding, set `WEB_HOST=127.0.0.1`. To access the LAN-bound
frontend from another machine, open `http://192.168.137.111:8200` or use an SSH
tunnel:

### Local HTTPS with Caddy

Phone browsers require a secure origin for microphone access. The workspace
includes a small Caddy reverse proxy using a locally trusted development CA:

```bash
./scripts/start-caddy-background.sh
./scripts/status-caddy.sh
```

Then use:

```text
https://icarus.local:8443
```

Caddy listens on port `8443` by default so it does not need privileged port
access. It proxies to the Python web server on `127.0.0.1:8200`; the Caddy
proxy and backend services are not otherwise changed. `tls internal` means the
certificate is self-signed by Caddy's local CA. For a phone to accept it
without a warning, install and trust Caddy's root certificate on that phone.

Find the certificate with:

```bash
./scripts/caddy-root-cert.sh
```

On iOS, install the downloaded `.crt` profile, then enable full trust under
Settings → General → About → Certificate Trust Settings. Android has a similar
certificate installation flow, although browser trust behavior varies by
version. Until the CA is trusted, `curl -k` and a browser warning can confirm
that the proxy itself is working, but microphone access may still be blocked.

The hostname defaults to `icarus.local`. Override it when starting Caddy if
needed:

```bash
CADDY_DOMAIN=192.168.137.111 ./scripts/start-caddy-background.sh
```

For a phone, `icarus.local` is preferable because the local certificate is
issued for that hostname and mDNS normally resolves it on the LAN. If the
phone cannot resolve `icarus.local`, add a local DNS/hosts entry or use an IP
site only after generating a certificate that includes that IP. Stop the
development proxy with `./scripts/stop-caddy.sh`.

The HTTPS frontend gives a clear error if microphone capture is attempted over
plain HTTP from another device. The HTTP endpoint remains available for
localhost/testing.

The page also offers a **Keep phone screen awake** option using the browser
Screen Wake Lock API. It is best-effort: it works in browsers that support the
API while the page is visible, and resumes after the page becomes visible again.
It cannot keep JavaScript, microphone capture, audio playback, or autonomous
checks alive after the phone is locked or the browser is suspended. A PWA does
not remove those operating-system restrictions; for reliable background voice
operation, a native app or platform-specific background audio/service mechanism
would be required.

To access the LAN-bound
frontend from another machine, open `http://192.168.137.111:8200` or use an SSH
tunnel:

```bash
ssh -N -L 8200:127.0.0.1:8200 edwardbeech@192.168.137.111
```

Stop the frontend with:

```bash
./scripts/stop-web.sh
```

## Spacebar input modes

The normal fixed-duration microphone mode records `RECORD_SECONDS` seconds.
For a more natural interface, the macOS keyboard listener supports global
spacebar control:

### Push-to-talk

Hold Space while speaking and release it to submit:

```bash
TTS_BACKEND=chatterbox ./scripts/voice-turn.sh --interactive --push-to-talk
```

Short form:

```bash
./scripts/voice-turn.sh --interactive --ptt
```

### Toggle-to-talk

Press Space to begin recording, then press Space again to submit:

```bash
TTS_BACKEND=chatterbox ./scripts/voice-turn.sh --interactive --toggle-to-talk
```

The listener uses a macOS global event tap, so the terminal does not need to
have focus. The first run may require enabling Accessibility permission for
the terminal or voice-chat process in **System Settings → Privacy & Security →
Accessibility**. Build the listener manually with:

```bash
./scripts/build-keyboard-listener.sh
```

The existing fixed-duration mode remains available:

```bash
./scripts/voice-turn.sh --interactive
```

## Resident inference services

The chat server, Whisper, and Chatterbox can run as separate long-lived local
processes. Start them once before an interactive voice session:

```bash
./scripts/start-chat-background.sh
./scripts/start-whisper-background.sh
TTS_BACKEND=chatterbox ./scripts/start-tts-background.sh

./scripts/status-chat.sh
./scripts/status-tts.sh
```

Whisper is resident on `127.0.0.1:8178` and Chatterbox Nano is resident on
`127.0.0.1:8190`. Their model weights are loaded once. `voice_loop.py` uses the
resident Whisper HTTP service by default (`STT_BACKEND=server`) and the
resident Chatterbox service when `TTS_BACKEND=chatterbox`; repeated turns should
therefore no longer print model-loading messages. Processed Chatterbox voice
references are also cached in the resident service, so the first turn with a
reference prepares its speaker conditioning and later turns reuse it.

Stop them with:

```bash
./scripts/stop-tts.sh
./scripts/stop-whisper.sh
./scripts/stop-chat.sh
```

The old one-shot paths remain available for diagnostics: use
`STT_BACKEND=cli` for `whisper-cli`, and invoke `src/chatterbox_tts.py` directly
for a one-shot Chatterbox generation.

## Chat server

The chat model is kept resident by `llama-server`; the voice loop only sends
HTTP requests to it. The managed launcher uses a practical 32K context instead
of the model's 262K maximum, keeps one slot, enables prompt caching, disables
model reasoning by default, and retains the existing MTP speculative decoder.
This is enough room for the in-memory conversation history while avoiding an
unnecessarily large KV-cache reservation.

Start it in the background:

```bash
./scripts/start-chat-background.sh
./scripts/status-chat.sh
```

Stop only a server started by the background launcher:

```bash
./scripts/stop-chat.sh
```

The chat server launcher defaults to localhost for safety. Set
`CHAT_HOST=0.0.0.0` only if the chat API itself must be reachable from another
machine; the browser frontend does not need this because it proxies chat,
Whisper, and TTS locally. The first launch can take time
to map the model; later launches reuse the local Hugging Face cache.

Useful server tuning variables:

```bash
CHAT_CTX_SIZE=32768       # use 65536 if longer conversations need it
CHAT_PARALLEL=1           # one interactive voice conversation
CHAT_SPEC_TYPE=draft-mtp
CHAT_SPEC_DRAFT_N_MAX=2
CHAT_REASONING=off
CHAT_CACHE_TYPE_K=q8_0
CHAT_CACHE_TYPE_V=q8_0
```

The web frontend normally allows up to 2,048 generated tokens. Requests that
look like long-form reproduction or reading requests (for example, “read this
in full” or “recite the whole thing”) use `CHAT_LONGFORM_MAX_TOKENS`, defaulting
to 4,096. The input context and output budget are separate: increasing the
output budget does not require the model to use all of it for ordinary replies.

To use another chat model, stop the managed server and start it with a new
Hugging Face selector:

```bash
CHAT_HF_REPO='owner/model:Q4_K_M' ./scripts/start-chat-background.sh
```

Or point directly at a local GGUF:

```bash
CHAT_MODEL_PATH="$HOME/path/to/model.gguf" ./scripts/start-chat-background.sh
```

`CHAT_MODEL` in the voice-loop environment is the model name sent in the API
request; it is not what loads the weights. The loaded weights are selected by
`CHAT_HF_REPO` or `CHAT_MODEL_PATH` when the server starts.

## TTS options

### Qwen3-TTS through llama.cpp

This is the default and has the lowest tested generation latency:

```bash
TTS_BACKEND=qwen ./scripts/voice-turn.sh --text "Hello from Qwen."
```

Relevant variables:

```bash
TTS_BACKEND=qwen
TTS_MODEL=models/Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf
TTS_MMPROJ=models/mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf
TTS_LANG=en
TTS_FRAMES=512
TTS_SPEAKER_FILE=/path/to/reference.wav  # optional cloning reference
```

### Chatterbox-Nano

This uses the resident Python/MPS Chatterbox runtime. The model is loaded once
by `start-tts-background.sh` and remains resident for the service lifetime. A
voice reference is processed on its first use and its speaker conditioning is
cached for subsequent turns. The model is already downloaded under
`models/chatterbox-nano`.

```bash
TTS_BACKEND=chatterbox \
  ./scripts/voice-turn.sh --text "Hello from Chatterbox Nano."
```

Relevant variables:

```bash
TTS_BACKEND=chatterbox
CHATTERBOX_MODEL=nano
CHATTERBOX_MODEL_DIR=models/chatterbox-nano
CHATTERBOX_DEVICE=mps       # mps or cpu
TTS_REFERENCE_CACHE_SIZE=4  # processed voice references retained by the service
TTS_SPEAKER_FILE=/path/to/reference.mp3
```

A reference clip longer than five seconds enables voice cloning. The tested
reference is:

```bash
TTS_SPEAKER_FILE="$HOME/Projects/Home/Orpheus-FastAPI/docs/TaraSad.mp3" \
  ./scripts/speak-chatterbox.sh "Hello in the reference voice."
```

`CHATTERBOX_MODEL=turbo` is supported by the wrapper, but its model files must
be downloaded separately and `CHATTERBOX_MODEL_DIR` must point at them. Nano is
currently the installed Chatterbox model.

Direct TTS commands:

```bash
./scripts/speak-qwen.sh "Hello from Qwen3-TTS."
./scripts/speak-chatterbox.sh "Hello from Chatterbox Nano."
afplay audio/chatterbox-nano.wav
```

## STT and audio variables

```bash
STT_MODEL=models/ggml-base.en.bin
STT_LANG=en
AUDIO_DEVICE=:1          # MacBook Pro Microphone in AVFoundation
RECORD_SECONDS=5
AFPLAY=afplay
```

The current STT implementation invokes `whisper-cli` directly. The existing
Whisper HTTP server remains available for separate integrations.

## Persona and current-session history

The primary `/` conversation frontend uses the fixed `DEFAULT_PERSONA` and does
not expose a persona editor. The full operational frontend at `/admin/` retains
the session-local persona editor for testing and administration. In both cases,
the web application retains a server-owned runtime contract for voice handling,
spoken output, supported vocal cues, and text reproduction. The hidden no-reply
and autonomous-turn policies are separate as well.

The standalone voice loop still accepts a process-local system prompt. It is
not saved by this repository:

```bash
CHAT_SYSTEM_PROMPT='You are a mischievous but useful voice assistant. Be concise.' \
  TTS_BACKEND=chatterbox ./scripts/voice-turn.sh --interactive
```

For a longer prompt, use a file:

```bash
CHAT_SYSTEM_PROMPT_FILE="$HOME/.config/voice-chat/system-prompt.txt" \
  ./scripts/voice-turn.sh --interactive
```

The interactive mode keeps conversational history in Python memory only and
sends it with each chat completion request:

```bash
./scripts/voice-turn.sh --interactive
```

By default it retains the last eight user/assistant turns. Change or disable
that behaviour with:

```bash
CHAT_HISTORY_TURNS=20 ./scripts/voice-turn.sh --interactive
CHAT_HISTORY_TURNS=0  ./scripts/voice-turn.sh --interactive
```

A text-driven session is useful for testing without the microphone:

```bash
./scripts/voice-turn.sh --interactive --text "Remember that my favourite colour is orange." --no-play
```

The first `--text` value is processed immediately; subsequent prompts are read
from the terminal. Exiting the process discards the system prompt and history.
A normal one-shot invocation (`--text`, `--audio`, or one microphone turn) also
has no persistent history. Browser session history remains process-local; the
primary conversation surface uses the fixed default persona, while `/admin/`
can edit the session persona. The runtime contract is server-owned in either
case.

The web service also archives the browser activity as local files. Each browser
session has a random 32-character hexadecimal ID in the `voicechat_session`
cookie, and the ID is included in the relevant directory and log filenames:

```text
voices/web/<session-id>/<UTC-timestamp>-reference-<random>.wav
audio/web/<session-id>/<UTC-timestamp>-input-<random>.wav
audio/web/<session-id>/<UTC-timestamp>-response-<random>.wav
logs/llm/<session-id>.jsonl
logs/sessions/<session-id>.json
```

The normalized clone reference, every normalized input voice recording, every
TTS output recording, and each LLM turn are retained. LLM JSONL records include
the UTC timestamp, session ID, turn kind, user/transcribed text, assistant text,
selected TTS backend, related media paths, and timing data. The session manifest
links the session ID to its creation timestamp and all three storage locations.
The server no longer deletes these files when the in-memory session TTL expires;
manual or later automated pruning is expected.

The web session also supplies a private timing hint after a configurable quiet
period (`CHAT_SILENCE_CONTEXT_AFTER`, default 10 seconds). Autonomous checks
repeat every `CHAT_AUTONOMOUS_INTERVAL` seconds, defaulting to the same
10-second value. If the model returns `<NO_REPLY>`, the web frontend skips TTS
and displays no assistant bubble.

## One-shot examples

```bash
# Record, transcribe, chat, synthesize, and play.
./scripts/voice-turn.sh

# Use an existing recording and do not play the response.
TTS_BACKEND=chatterbox ./scripts/voice-turn.sh \
  --audio audio/qwen-smoke.wav --no-play

# Keep a newly recorded input file for inspection.
./scripts/voice-turn.sh --keep-audio --no-play
```

## Downloads and checks

```bash
./scripts/download-whisper-base.en.sh
./scripts/download-qwen-tts.sh
./scripts/download-chatterbox-nano.sh
./scripts/check-host.sh
```
