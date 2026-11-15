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

## Measured on the GPU, 2026-11-15 — and two corrections to the above

**The desktop session is not holding 5.7 GB of the card.** With root, `nvidia-smi`'s full
process table — which does include type `G`, graphics clients — attributes **817 MiB** on
august-desktop: three camry object-detector contexts at 240 MiB each, plus gnome-shell at
97 MiB. Killing one detector's context from the host took `memory.used` 813 → 573 MiB and
back 45 s later when the container restarted it, so 240 MiB is exactly what each costs.
Chrome and its GPU process never appear on the NVIDIA card at all: this box is a 5600G with
a Cezanne Vega iGPU (`card1`), and the monitor is on the NVIDIA HDMI (`card2-HDMI-A-2`).

What *is* true is that the card reached **7605/8192 MiB used** with only that 817 MiB
attributable — ~6.8 GiB of VRAM no process would name — and a **reboot** was what cleared it,
not any pod restart. Unexplained, and the leading suspect is orphaned contexts from container
churn on the node (the nvidia-device-plugin pod there has 10 restarts, persistence mode off,
driver 595.99.02). `/usr/local/bin/gpu-watch.sh`, run every minute by `/etc/cron.d/gpu-watch`,
now logs total-vs-attributed so the next occurrence shows what grows instead of being guessed
at after a reboot destroys the evidence.

**The CUDA work was not a llama.cpp task**, because the shipping backend is Chatterbox, and
that service already took a device: `CHATTERBOX_DEVICE=cuda` is the whole change (plus the
CUDA torch wheel in the image and an `nvidia.com/gpu` request). Measured on this node's
**RTX 2080 SUPER**, 8192 MiB, driver 595.99.02, torch 2.6.0+cu124 (`sm_75` in the arch list):

| | CPU (5600G, 8 threads) | GPU (RTX 2080 SUPER) |
|---|---|---|
| wall clock per turn | 13.1 s for 5.4 s of audio | 3.1 s for 9.16 s of audio |
| RTF | 2.4 | **0.34** |
| model load | resident, ~40 s cold | 6.9 s cold |
| first `prepare_conditionals` | 11.6 s | 10.3 s |
| VRAM | n/a | **3159 MiB peak, 3304 MiB reserved** |

Roughly **7x** faster per turn, which is the difference between a 13 s tail and a 3 s one.
Note the conditioning step barely moved: it is reference-file decode and encoder work, most of
which is not the GEMM bulk, so paying 10.3 s once at boot still matters.

fp16 would have halved the footprint and **is not available**: `from_local()` in
chatterbox-tts 0.1.7 takes only `(ckpt_dir, device)` with no `half=` for Turbo, and casting
`t3`/`s3gen`/`ve` by hand fails inside generation with `RuntimeError: Unsupported dtype Half`.
Load order also matters: `from_local(dir, "cuda")` sets the wrapper's own `.device`, which is
what `generate()`/`prepare_conditionals()` use to place their tensors — loading on CPU and
then moving the submodules leaves the wrapper feeding CPU inputs to GPU weights
(`Input type (FloatTensor) and weight type (cuda.FloatTensor) should be the same`).

Because time-slicing isolates no memory, the pod caps itself at `CHATTERBOX_CUDA_MEM_FRACTION`
(0.55 = 4.5 GiB against that 3.3 GiB peak) and runs with
`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` so the allocator shrinks mappings instead
of parking blocks. Rollback is two values in the manifest: image back to `0.1.7` and
`CHATTERBOX_DEVICE` back to `cpu`.

### What the pod itself measured, on the same day

The table above was taken outside the cluster. After the changeover landed on
`edvox-tts-chatterbox` in `home-dev`, the shipping path measured:

| | outside the cluster | inside the pod |
|---|---|---|
| model load cold | 6.9 s | 7.8 s |
| reference conditioning, first time | 10.3 s | 9.9 s |
| synthesis | 3.1 s for 9.16 s (RTF 0.34) | 2.11 s for 5.72 s (**RTF 0.37**) |
| VRAM held | 3304 MiB reserved | 3308-3328 MiB, released to 732 MiB on pod deletion |

and one real turn through `POST /api/chat` on `edvox-web` - chat plus speech, not speech alone -
came back in **1.87 s total** (`chat_ms` 417, `tts_ms` 1457 for 4.16 s of audio) against the
8.7-13.3 s the CPU build used to spend. `nvidia-smi` during that turn showed our context at
3308 MiB alongside the three camry detector contexts at 240 MiB each, 3744 MiB still free, and
deleting the validation pod returned the card to 732 MiB with nothing left attributed, so the
allocator bound and the exit path both behave.

The one thing this deployment owes: `0.1.7-cuda` is only in august-desktop's docker store, so
the manifest runs `imagePullPolicy: IfNotPresent` until the Docker Hub upload - capped near
200 KiB/s by the registry's 60 KiB receive window at 246 ms RTT - finally publishes the tag.
Until then a store wipe on that node has no upstream image to recover from.
