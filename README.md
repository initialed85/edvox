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

## Pipeline components

```text
microphone -> Whisper STT -> llama-server:8088 -> Qwen3-TTS -> speakers
```

The chat model, STT model, and TTS model are separate processes/backends so each can be replaced independently.

## Initial local components

- Chat: existing llama-server at `http://192.168.137.111:8088/v1`
- STT: whisper.cpp base English model
- TTS: llama.cpp `llama-tts` with Qwen3-TTS 1.7B Base
- Capture: ffmpeg + macOS AVFoundation (`:1` is the MacBook Pro Microphone)
- Playback: macOS `afplay`

## Speech-to-text

Transcribe an existing audio file locally:

```bash
./scripts/transcribe.sh audio/qwen-smoke.wav
```

Record five seconds from the built-in microphone and transcribe it:

```bash
./scripts/record-and-transcribe.sh
```

Change recording length or device:

```bash
RECORD_SECONDS=10 AUDIO_DEVICE=:1 ./scripts/record-and-transcribe.sh
```

List AVFoundation devices:

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

Start the optional Whisper HTTP service:

```bash
./scripts/start-whisper.sh
```

Then submit an audio file from another shell:

```bash
./scripts/transcribe-http.sh audio/qwen-smoke.wav
```

## Text-to-speech

```bash
./scripts/speak-qwen.sh "Hello from the local voice interface."
afplay audio/test.wav
```

For a reference voice:

```bash
TTS_SPEAKER_FILE=/path/to/reference.mp3 \\
  ./scripts/speak-qwen.sh "Hello in the reference voice."
```

## Download speech models

```bash
./scripts/download-whisper-base.en.sh
./scripts/download-qwen-tts.sh
```
