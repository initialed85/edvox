# Sizing the voice loop on `august-desktop`

Measured on the node the pipeline actually runs on (Ryzen 5 5600G, 12 logical cores,
30 GB RAM, AMD TUF Gaming RX 5500), not on the laptop the daemon was developed on.
Model: `Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf` + `mmproj-...-Q8_0.gguf`, Aussie speaker
reference, 128-frame cap. One text, one reference, one configuration change at a time.

| configuration | peak RSS | generation | vocoder | speaker prompt |
| --- | --- | --- | --- | --- |
| 8 threads, KV `q8_0`/`q4_0`, ctx 4096 — **as deployed** | **5817 MB** | 65 ms/frame | 48 ms/frame | ~0.96 s |
| 12 threads, same KV/ctx | 5819 MB | 98 ms/frame (slower) | 47 ms/frame | ~1.05 s |
| 12 threads, ctx 2048 | 5818 MB | 69 ms/frame | 45 ms/frame | ~0.98 s |
| 12 threads, KV left f16 | **7947 MB** | 71 ms/frame | 47 ms/frame | ~0.96 s |

Frame = 1/12.5 s of speech, so 80 ms of audio per frame.

## What the numbers say

- **Keep 8 threads.** 12 threads on 6 physical cores made generation *slower* per frame
  (98 vs 65 ms). The node also has a desktop session on it. Requests 2 CPU / limit 10 is
  enough for the 8 the daemon is given.
- **Keep the quantised KV cache.** It buys ~2.1 GB of RAM (5817 vs 7947 MB) for free —
  no measurable speed cost.
- **ctx 4096 vs 2048 does not matter here.** Peak RSS moved by 1 MB. The talker and codec
  buffers dominate, not the attention cache. Stay at 4096 for longer replies.
- **Memory request is 6 Gi, limit 8 Gi.** The old 4 Gi request under-booked the node
  against a measured 5817 MB peak.

## Cost per turn

Speech costs about **1.4× realtime in CPU time** (0.81 s of generation + 0.6 s of vocoder
per second of speech) plus a fixed ~1 s speaker-prompt stage. End-to-end, measured through
the running service with real audio:

```
11.35 s of Ed's voice  ->  STT 0.83 s  ->  chat 0.48 s  ->  TTS 8.79 s  =  10.4 s
                                                   (5.92 s of spoken reply)
```

Short reply (~2 s of speech) lands around 4-5 s; a long one (~8 s) around 12 s. That is
walkie-talkie pace: fine for a hands-free back-and-forth, not conversational.

## The decision this produces

Nothing to buy to make it run — it runs, it is resident, and the brain is free (the
existing `sentiment-analyzer` llama-server on `hermes` did 80-89 tok/s on this and added
0.5 s). The sizing question is about **speed**, and the answer is a GPU:

- Qwen3-TTS needs about **2.5-3 GB of VRAM** for weights (1.04 GB talker Q4_K_M, 425 MB
  codec Q8, KV and buffers) — the 8 GB RTX 2080 already earmarked for this node fits it
  with room to spare, and even the existing 6 GB card would, except its 5.7 GB is held by
  the desktop session.
- That card is the difference between "walkie-talkie" and "conversation". It is a
  hardware purchase Ed already planned; no additional spend is implied by these numbers.
- Software side, when the card is in: this container is CPU-only and the host's ROCm build
  segfaults on that NVIDIA card, so it needs a **CUDA** llama.cpp build (`llama-core-cuda`
  image, `llama-tts-cuda` binary, `nvidia.com/gpu` resource request). That is the one open
  engineering task, and it is worth re-running this table on the GPU before trusting any
  predicted speed-up.
