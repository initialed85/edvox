# voice-chat

A local voice interface for the llama.cpp chat server on the Apple Silicon
host. The components are deliberately replaceable:

```text
microphone -> Whisper STT -> llama-server chat -> TTS backend -> speakers
```

## Web frontend

A local browser frontend is available on port `8200`. It provides session-local
backend selection, system prompts, voice-reference upload, text chat, generated
audio playback, and a push-to-talk button. The browser microphone permission is
ordinary `getUserMedia` permission; no macOS Accessibility permission is needed
for the button. The default web binding is `0.0.0.0`, so the UI can be opened
from another device on the same LAN at `http://192.168.137.111:8200`. The
backend services remain on loopback; only the browser-facing web UI is exposed.
If macOS prompts to allow incoming connections, permit Python for the local
network. Do not forward this port to the public internet without adding
authentication and HTTPS. Spacebar PTT is opt-in via the `Enable Spacebar PTT` checkbox;
this prevents the browser's normal Spacebar scroll behavior from being hijacked
unless explicitly enabled. The settings panel also has `Record my voice`, which
records a browser microphone clip and installs it as the session's clone voice.
Press Enter in the message box to send; use Shift+Enter for a multiline message.

### VOX mode

The browser also has an opt-in **Enable VOX** mode. After microphone permission
is granted, the browser analyzes the microphone signal locally and uploads only
completed speech segments. The conversation area has its own scrollable history,
so long conversations stay inside the browser viewport. It adapts to the local noise floor, keeps a short
pre-roll, waits through a short conversational backoff before applying about
1.4 seconds of sustained silence, and caps a single utterance at 30 seconds. Empty, silence-only, and short filler-only Whisper transcripts
are discarded by the web server before the chat model or TTS is called.

VOX is intentionally half-duplex for now: it pauses listening while the
assistant's audio reply plays, then arms itself again. Disable VOX to return to
button PTT or ordinary text chat.

New sessions start with a voice-oriented system prompt that can be edited or
replaced in the session settings. Chatterbox Nano and Turbo officially document
these paralinguistic tags: `[cough]`, `[laugh]`, `[chuckle]`, `[clear throat]`,
`[sigh]`, `[shush]`, `[groan]`, `[sniff]`, and `[gasp]`. Do not use arbitrary
bracketed directions such as `[slight pause]`; unsupported text may be spoken
literally. Use ordinary punctuation for pauses.

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

## System prompt and current-session history

The voice loop accepts a system prompt. It is process-local and is not saved
by this repository:

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
has no persistent history.

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
