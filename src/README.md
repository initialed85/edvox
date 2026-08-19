# Orchestrator design

The first executable milestone is a text-to-speech smoke test. The next component should be a small asyncio process with replaceable adapters:

- `chat_client`: OpenAI-compatible streaming requests to `CHAT_BASE_URL`
- `stt_client`: multipart requests to whisper-server `/inference`
- `tts_client`: subprocess initially, HTTP service later
- `audio_io`: CoreAudio capture/playback with cancellation
- `turn_state`: IDLE → LISTENING → TRANSCRIBING → THINKING → SPEAKING

Keep the TTS adapter boundary independent of the llama.cpp CLI so Chatterbox can be added without redesigning the conversation loop.
