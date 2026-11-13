# Running edvox in the cluster

Three images, three Deployments, one namespace (`edvox` on home-dev), all pinned to
`august-desktop`:

| component | image | port | what it is |
| --- | --- | --- | --- |
| web | `docker.io/initialed85/edvox-web` | 8200 | `src/web_server.py`, browser sessions, turn orchestration |
| tts | `docker.io/initialed85/edvox-tts` | 8191 | `native/ttsd.cpp`, resident Qwen3-TTS daemon |
| stt | `docker.io/initialed85/edvox-whisper` | 8178 | `whisper-server`, resident whisper.cpp |

Manifests: `home-ops/clusters/dev/applications/edvox/` (ArgoCD app `edvox`).
Public URL: `https://edvox.dev.initialed85.cc` (Traefik terminates TLS, which is what
`getUserMedia` needs for the microphone).

## Build and roll out

```sh
# from an x86-64 box - august-desktop is amd64 and llama.cpp is not worth emulating
./scripts/build-images.sh                    # build + push all three
kubectl -n edvox rollout restart deploy/edvox-tts   # :latest + imagePullPolicy: Always
```

The llama.cpp build inside `docker/Dockerfile.tts` is pinned to
`9b8446b91434e0c0cdc293baf0b29c099eaaa0c7` because the latency figures below were
measured on it. Bump deliberately and re-measure: the codec stage is where a regression
hides.

## Why there is a daemon at all

`llama-tts` is one-shot. Every utterance pays model load, mmproj load and graph setup,
which measured **2.4-3.4 s on top of a 6-14 s synthesis** on CPU-only hardware. In a
conversation that is the difference between a voice assistant and a paused recording.

`native/ttsd.cpp` loads the backbone and codec once, then serves `/synthesize` from
resident weights, resetting sampler, KV memory and codec state between turns so previous
turns cannot leak into the next one. Post-load, the measured turn on the same box:

```
text "Container smoke test, mate."  ->  1.92 s audio in 6.67 s
    prompt  0.67 s   (clip-encode of the speaker reference)
    gen     2.08 s   (1.7B talker, Q4_K_M)
    vocoder 3.92 s   (12 Hz codec -> 24 kHz PCM)   <- the expensive half
```

## Model and voice files

- Weights are fetched by an init container into `hostPath`
  `/home/edward/.cache/edvox/models` on `august-desktop`, from
  `ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF` (Q4_K_M + Q8_0 codec) and
  `ggerganov/whisper.cpp` (`ggml-base.en.bin`).
- Session audio, uploaded voice references and logs live on the `edvox-data` PVC
  (`nfs-storage-ssd`, RWX), mounted by the web and TTS pods at the same paths, because
  the TTS contract here is path-based.
- Named clone voices are configured with `EDVOX_SPEAKER_DIR` + `EDVOX_SPEAKERS`. The
  **first entry is the default voice** for a session that has not uploaded a reference,
  so list order matters.

## Retuning without rebuilding

Everything the daemons need is env: `TTS_THREADS`, `TTS_CTX_SIZE`, `TTS_CACHE_K`,
`TTS_CACHE_V`, `TTS_MAX_FRAMES`, `WHISPER_THREADS`. When a GPU with genuinely free VRAM
lands on this node, `TTS_GPU_LAYERS=99` and `TTS_DEVICE=auto` is the whole change - the
image is a CPU-only build but the same binary runs on GPU.

`llama-tts` CPU inference cannot use ROCm: on the AMD-GPU machines here `llama-tts`
aborts on a `hip_code_object` assertion (`err == hipSuccess`) before producing a frame,
which is why the image builds with `GGML_HIP=OFF`. august-desktop's only GPU is a
GTX 1660 SUPER and 5.7 GB of its 6 GB is held by its own desktop session, so there is
nothing to offload to either way.

## Measured sizing

[docs/tts-sizing.md](tts-sizing.md) has the numbers from `august-desktop`: peak RSS per
thread count, KV-quantisation effect, cost per turn, and why the pending RTX 2080 is a
speed decision rather than a "will it run" decision.
