# voice-chat

Local voice interface for the llama.cpp chat server on `192.168.137.111:8088`.

## Current layout

- `bin/` — local inference binaries (`llama-tts`, `whisper-server`, `whisper-cli`)
- `models/` — speech models; keep large downloaded weights out of git
- `voices/` — optional reference recordings for voice cloning
- `audio/` — generated and captured audio
- `src/` — orchestrator, HTTP adapters, audio I/O, and turn state machine
- `scripts/` — setup and service launch scripts
- `logs/` — runtime logs

## Planned pipeline

```text
microphone -> VAD/STT -> llama-server:8088 -> TTS -> speakers
```

The chat model, STT model, and TTS model are intentionally separate processes/backends so each can be replaced independently.

## Initial local components

- Chat: existing llama-server at `http://192.168.137.111:8088/v1`
- STT: whisper.cpp (`whisper-server`)
- TTS: llama.cpp `llama-tts` with Qwen3-TTS 1.7B Base
- Playback/capture: macOS `afplay`/CoreAudio; ffmpeg is available for conversion

## Download speech models

```bash
./scripts/download-whisper-base.en.sh
./scripts/download-qwen-tts.sh
```

The Qwen3-TTS setup uses the llama.cpp-compatible 1.7B Base Q4 model plus its Q8 multimodal projector (about 1.4 GiB total). A reference recording can be supplied with `TTS_SPEAKER_FILE` for voice cloning.

## Generate and play a test

```bash
./scripts/speak-qwen.sh "Hello from the local voice interface."
afplay audio/test.wav
```

For a reference voice:

```bash
TTS_SPEAKER_FILE=/path/to/reference.mp3 \\
  ./scripts/speak-qwen.sh "Hello in the reference voice."
```
