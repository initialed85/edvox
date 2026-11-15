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

### If august-desktop's docker store loses the CUDA image

The Chatterbox CUDA image is 3.64 GB. The tags are published on Docker Hub, but re-pulling 3.64 GB
over this house's upstream link during an outage is not something to attempt under pressure, so a
flat copy of the shipping image also lives on the `edvox-data` PVC, which is NFS-backed and therefore
lives on the NAS rather than on the node:

```sh
# on august-desktop - find where the PVC is mounted, then load from it
M=$(grep -m1 pvc-aa431866 /proc/mounts | cut -d' ' -f2)
sudo docker load -i "$M/image-backups/edvox-tts-chatterbox-0.1.7.2-cuda.tar"
# Loaded image: initialed85/edvox-tts-chatterbox:0.1.7.2-cuda   -> id da6fb5d8ba7f, same as the hub tag
```

On the NAS itself the file is `192.168.1.25:/storage-hdd/pvc-aa431866-4ed9-4e5f-b79d-dfd649747207/image-backups/`,
so the copy survives the node entirely. It was made with `docker save <tag> -o $M/image-backups/...tar`
and load-tested back into the store to confirm it yields the same image id.

Two figures that tie the recovery paths together, both measured on august-desktop on 2026-11-15: the
tar is 3,639,093,760 bytes and Docker Hub reports the published tag's `full_size` as 3,639,059,797;
`docker pull initialed85/edvox-tts-chatterbox:0.1.7.2-cuda` on the node returns
`Digest: sha256:da6fb5d8ba7f…` / "Image is up to date", which is the same id the tar restores to.
So the hub is the primary source again and the NAS copy is the one that does not depend on this
house's upstream link.

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

## Changing the speech model

[tts-changeover-spec.md](tts-changeover-spec.md) is the handover plan for replacing the
TTS model: the measured A/B harness, the listening gate that belongs to Ed, the two
plumbing fixes required first (per-backend `TTS_URL_*`, one cue-support map), the
deployment shape for each candidate, and the rollback. Written to be executed by an agent
with no prior context — execute the phases in order and do not substitute the decision.

### Chatterbox has two wheel variants of the same image

`docker/Dockerfile.tts-chatterbox` builds either one from a single file:

```sh
# CPU wheel, tag 0.1.7 - what shipped first, and the rollback
docker build -f docker/Dockerfile.tts-chatterbox -t docker.io/initialed85/edvox-tts-chatterbox:0.1.7 .
# CUDA wheel, tag 0.1.7-cuda - what august-desktop runs now on its RTX 2080 SUPER
docker build -f docker/Dockerfile.tts-chatterbox \
  --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124 \
  --build-arg DEFAULT_DEVICE=cuda \
  -t docker.io/initialed85/edvox-tts-chatterbox:0.1.7-cuda .
```

The device is baked into each variant via `CHATTERBOX_DEVICE` so a CUDA image cannot silently
serve CPU on a GPU node, and the deployment states it again so `kubectl get deploy -o yaml`
shows the value that decides where synthesis runs. Moving between the two is those **two
values** in `home-ops` `clusters/dev/applications/edvox/deployment-tts-chatterbox.yaml` — image
tag and `CHATTERBOX_DEVICE` — nothing else. The deployment is `strategy: Recreate`, so a
rollout leaves a gap of roughly a minute while the new pod loads weights (6.9 s on the 2080
SUPER) and conditions the warm-up reference (10.3 s). Measured footprint, the 7x speed-up and
the fp16 dead end are in `docs/tts-sizing.md`.
