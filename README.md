# voice-chat

Local voice interface for the llama.cpp chat server on .

## Pipeline components



The chat, STT, and TTS backends are independent. The current TTS choices are
Qwen3-TTS through llama.cpp and Chatterbox-Nano through its native PyTorch/MPS
runtime.

## Speech-to-text


 Hello Edward, this is a local voice test.
 you

The built-in MacBook Pro Microphone is AVFoundation device .

## Text-to-speech

Qwen3-TTS:

audio/test.wav

Chatterbox-Nano:



Chatterbox supports native paralinguistic tags such as , ,
and :



Use a reference recording for voice cloning. Chatterbox requires a reference
clip longer than five seconds:



The Chatterbox runtime uses the dedicated Python 3.11 environment at
, PyTorch 2.6, and MPS on the Apple Silicon host. The Nano
model is stored in  and is approximately 2.8 GiB.

## One-shot voice turn

The complete loop records, transcribes, chats, synthesizes, and plays:

You: you
Assistant: Hello! How can I help you today? 😊
Audio: /Users/edwardbeech/Projects/Home/voice-chat/audio/response-20260819-224225.wav

Use Chatterbox-Nano for the TTS response:

loaded PerthNet (Implicit) at step 250,000
S3 Token -> Mel Inference...
/Users/edwardbeech/Projects/Home/voice-chat/audio/response-20260819-224238.wav
You: you
Assistant: Hi! How can I help you today?
Audio: /Users/edwardbeech/Projects/Home/voice-chat/audio/response-20260819-224238.wav

A text-only smoke test avoids the microphone:

loaded PerthNet (Implicit) at step 250,000
S3 Token -> Mel Inference...
/Users/edwardbeech/Projects/Home/voice-chat/audio/response-20260819-224301.wav
You: Say hello in one short sentence.
Assistant: Hello!
Audio: /Users/edwardbeech/Projects/Home/voice-chat/audio/response-20260819-224301.wav

An existing audio file can exercise the complete STT -> chat -> TTS path:

loaded PerthNet (Implicit) at step 250,000
You: Hello Edward, this is a local voice test.
Assistant: Hello! This is Edward, your AI assistant. How can I help you today? Whether you have questions, need assistance with a task, or just want to chat, I'm here for you.

Set  to a WAV or MP3 reference recording to enable cloning.
Use  to retain a newly recorded microphone turn.

## Setup/download scripts


