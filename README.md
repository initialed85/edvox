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
- TTS: llama.cpp `llama-tts` with Qwen3-TTS, or a Chatterbox backend later
- Playback/capture: macOS `afplay`/CoreAudio; ffmpeg is available for conversion

## Disk warning

The Mac currently has only about 3 GiB free. Do not download multi-gigabyte model files until old files are cleaned up or more storage is available. The planned Qwen3-TTS 0.6B/Q8 + codec setup is roughly 1.3 GiB; a 1.7B setup is roughly 2.2 GiB before STT models and caches.
