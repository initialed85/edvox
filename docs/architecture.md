# Voice-chat architecture

This document uses three views:

1. **System context**: the voice-chat system as a black box and the systems around it.
2. **System diagram**: the internal runtime pieces and their boundaries.
3. **Sequences**: the two flows that are easiest to misunderstand: a VOX turn and an autonomous check-in.

The diagrams describe the browser-based deployment on `icarus.local`.

## 1. System context — black box

```mermaid
flowchart LR
    user([User])
    browser[Phone or desktop browser]
    gateway[Caddy HTTPS gateway\nicarus.local:8443]
    system{{Local voice-chat system}}
    chat[llama-server\nchat model\n127.0.0.1:8088]
    stt[Whisper server\nSTT model\n127.0.0.1:8178]
    tts1[Chatterbox service\nresident TTS and voice cloning\n127.0.0.1:8190]
    tts2[Qwen3-TTS\none-shot llama-tts backend]
    files[(Local session and\naudio files)]

    user -->|speaks, listens, types| browser
    browser -->|HTTPS| gateway
    gateway -->|reverse proxy| system
    system -->|chat completions| chat
    system -->|speech transcription| stt
    system -->|synthesis, optional cloned voice| tts1
    system -->|alternative synthesis| tts2
    system -->|session state and generated audio| files
```

The browser is the only client-facing part. The inference services are local
supporting systems; they are not exposed directly to the browser.

## 2. System diagram — white box

```mermaid
flowchart TB
    subgraph Client[Browser client]
        ui[Web UI]
        text[Text composer]
        ptt[Push-to-talk and clone recording]
        vox[VOX detector\ngetUserMedia + local RMS/VAD]
        auto[Autonomous timer\nquiet-period checks]
        playback[Audio playback]
    end

    subgraph Edge[HTTPS edge]
        caddy[Caddy\ntls internal\n:8443]
    end

    subgraph App[Voice-chat application host]
        web[Python web_server.py\nHTTP API :8200]
        sessions[In-memory sessions\nconversation history\nsystem prompt\nreference path\nactivity timestamps]
        policy[Response handling\nno-reply markers\nquiet-time policy\nTTS selection]
        media[Media and file handling\nffmpeg normalization\nper-session WAV/audio files]
        whisperClient[whisper_client.py\nHTTP STT adapter]
        qwenProc[llama-tts subprocess\nQwen3-TTS one-shot adapter]
    end

    subgraph Services[Resident or local inference services]
        llama[llama-server\nOpenAI-compatible chat API\n:8088]
        whisper[whisper-server\n/inference\n:8178]
        chatter[Chatterbox HTTP service\nresident model and reference cache\n:8190]
        qwenModels[Qwen3-TTS model plus mmproj]
    end

    storage[(voices/web/<session>/reference.wav\naudio/<session>/response-*.wav)]

    user([User]) --> ui
    ui --> text
    ui --> ptt
    ui --> vox
    ui --> auto
    playback --> user

    ui -->|HTTPS| caddy
    caddy -->|reverse proxy| web

    text -->|POST /api/chat| web
    ptt -->|POST /api/voice-turn| web
    vox -->|speech segment only\nPOST /api/voice-turn| web
    auto -->|POST /api/autonomous-turn| web
    playback <-->|audio URL| web

    web --> sessions
    web --> policy
    web --> media
    web -->|chat completion| llama
    web -->|invoke| whisperClient
    whisperClient -->|multipart /inference| whisper
    policy -->|resident /synthesize| chatter
    policy -->|invoke one-shot| qwenProc
    qwenProc --> qwenModels
    media --> storage
    sessions --> storage

    classDef client fill:#e8f1ff,stroke:#3569a8,color:#10243e
    classDef app fill:#eef8ed,stroke:#4d8a4a,color:#183519
    classDef service fill:#fff4df,stroke:#b27618,color:#3e2907
    classDef storage fill:#f3eafa,stroke:#8155a6,color:#28143b
    class ui,text,ptt,vox,auto,playback client
    class web,sessions,policy,media,whisperClient,qwenProc app
    class llama,whisper,chatter,qwenModels,caddy service
    class storage storage
```

### Runtime boundaries

- **Browser** owns microphone permission, local VOX analysis, recording, the
  autonomous timer, and playback. It does not call the model services directly.
- **`web_server.py`** owns cookie-scoped sessions, history, prompts, quiet-time
  eligibility, no-reply handling, media conversion, and orchestration.
- **Whisper** is resident and receives normalized audio through its HTTP
  `/inference` endpoint.
- **Chatterbox** is resident and keeps the loaded TTS model and processed voice
  conditionals in memory. Qwen3-TTS remains a separate one-shot subprocess
  alternative.
- **`llama-server`** is resident and is the only chat-model process in the
  normal browser path.
- **Session memory is process-local**. Restarting the web server discards
  conversations, system prompts, timestamps, and active session associations.

## 3. Sequence — normal VOX turn

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Browser as Browser VOX
    participant Web as web_server.py
    participant STT as whisper-server
    participant Chat as llama-server
    participant TTS as Chatterbox or Qwen
    participant Store as Session/audio files

    User->>Browser: Speak
    Browser->>Browser: RMS/VAD detects onset
    Browser->>Browser: Buffer pre-roll and speech
    Browser->>Browser: Wait for trailing silence or 30-second cap
    Browser->>Web: POST /api/voice-turn with audio segment
    Web->>Web: Normalize upload with ffmpeg
    Web->>STT: POST /inference with WAV
    STT-->>Web: Transcript
    alt Empty, silence-only, or filler-only transcript
        Web-->>Browser: ignored=true
        Browser->>Browser: Return to armed listening
    else Meaningful transcript
        Web->>Store: Append user turn and session context
        Web->>Chat: POST /v1/chat/completions
        Chat-->>Web: Assistant text or <NO_REPLY>
        alt No reply marker
            Web->>Store: Record silent assistant turn
            Web-->>Browser: no_reply=true
        else Spoken reply
            Web->>TTS: Synthesize assistant text
            TTS-->>Web: WAV path or output
            Web->>Store: Save response audio
            Web-->>Browser: Reply text and audio URL
            Browser->>Browser: Play audio
        end
        Browser->>Browser: Resume armed VOX after playback
    end
```

VOX is half-duplex: the browser does not listen for a new utterance while an
assistant response is being processed or played. Local detection prevents idle
microphone audio from being uploaded, while the server-side transcript filter
is the final guard before chat and TTS.

## 4. Sequence — autonomous check-in

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Browser as Browser JavaScript
    participant Web as web_server.py
    participant Chat as llama-server
    participant TTS as Chatterbox or Qwen

    User->>Browser: Complete a real text or voice turn
    Browser->>Browser: Reset timer and remain armed if VOX is enabled
    loop Every autonomous interval after the quiet threshold
        Browser->>Browser: Timer fires while page is open
        alt VOX recording, processing, speaking, another request, or audio playback
            Browser->>Browser: Defer and schedule another check
        else VOX armed or disabled
            Browser->>Web: POST /api/autonomous-turn
            Web->>Web: Check last_activity, threshold, and interval
            alt Not eligible
                Web-->>Browser: skipped=true
            else Eligible
                Web->>Chat: Private autonomous prompt plus elapsed quiet context
                Chat-->>Web: <NO_REPLY> or natural response
                alt <NO_REPLY>
                    Web-->>Browser: no_reply=true; no audio
                else Response text
                    Web->>TTS: Synthesize response
                    TTS-->>Web: Audio output
                    Web-->>Browser: Text and audio URL
                    Browser->>User: Display and play response
                end
            end
        end
    end
    User->>Browser: Speak or type again
    Browser->>Web: Normal chat or voice-turn request
    Browser->>Browser: Reset autonomous schedule
```

The wake-up mechanism is therefore **frontend timer plus backend eligibility
check**. The backend does not wake itself and cannot initiate a request while
no browser page is open. The model is invoked on every eligible interval; it is
free to return `<NO_REPLY>` or respond based on the accumulated quiet-time
context.

## Ports and bindings

| Component | Default binding | Role |
|---|---:|---|
| Caddy | `0.0.0.0:8443` | LAN HTTPS entry point and reverse proxy |
| Web server | `0.0.0.0:8200` | Browser UI and orchestration API |
| llama-server | `127.0.0.1:8088` | Resident chat model |
| Whisper server | `127.0.0.1:8178` | Resident speech-to-text model |
| Chatterbox | `127.0.0.1:8190` | Resident speech synthesis and voice cloning |
