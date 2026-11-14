# TTS changeover — specification and execution plan

**Status:** draft, waiting on Phase 0.
**Written:** 2026-11-14, against `edvox` head `622978a`.
**Audience:** an execution agent with no access to the conversation that produced this
document. Every fact you need is restated here; read the whole thing before touching
anything, then follow the phases in order.

---

## 0. How to use this document

Execute phases **in order**. Phase 0 is measurement, Phase 1 is a decision that belongs to
Ed (not to you), Phases 2–3 are implementation of whatever Phase 1 chose.

Rules that override any instinct to be helpful:

- **Do not skip Phase 1.** You cannot decide which voice is better by reading a
  leaderboard, and you must not guess on Ed's behalf. Phase 1 produces a judgement from
  Ed's ears or it stays blocked.
- **Do not delete the working pipeline.** `qwen-service` stays functional and one env
  change away until Phase 3 passes every acceptance check.
- **Do not start extra benchmarks.** Phase 0 lists exactly which models to test. Adding a
  seventh because it looks promising is out of scope; write it in §9 instead.
- If a step's stated command fails, capture the error verbatim, try **at most twice**
  more, then stop and report. Do not improvise a different model, runtime, or quant.
- Anything ambiguous that is not covered here: **ask Ed, do not assume.**

Report at the end using the template in §10.

---

## 1. Current state (verified, do not re-derive)

| Item | Value |
| --- | --- |
| Repo | `~/Projects/Home/edvox`, branch `master`, head `622978a` |
| Deployment repo | `~/Projects/Home/home-ops`, `clusters/dev/applications/edvox/` |
| Cluster | `home-dev`, namespace `edvox`, node `august-desktop` |
| Public URL | `https://edvox.dev.initialed85.cc` |
| Node | Ryzen 5 5600G, 6 cores / 12 threads, 30 GB RAM, RX 5500. **RTX 2080 8 GB is pending, not installed** |
| Node taint | `desktop-node=true:NoSchedule` — deliberate; all three edvox pods carry explicit tolerations |
| PVC | `edvox-data`, NFS RWX, 5 Gi, subPaths `audio/` and `voices/` |
| Model cache | hostPath `/home/edward/.cache/edvox/models` (init container downloads into it) |
| Services | `voice-chat:80` → web, `edvox-tts:8191` → TTS daemon, `edvox-whisper:8178` |
| Voice refs | `voices/clone/aussie-ref.wav` (default), `kajira-ref.wav`, `dainty-ref.wav` on the PVC; sources in `voices/` |

### The three TTS backends that already exist in `src/web_server.py`

| `session.backend` | How it synthesises | Port |
| --- | --- | --- |
| `qwen` | spawns `llama-tts` per request (load penalty each turn) | — |
| `qwen-service` | POSTs to a resident llama.cpp daemon; **what production runs** | 8191 |
| `chatterbox` | POSTs to a resident Python Chatterbox server | 8190 |

Selection: `WEB_DEFAULT_TTS_BACKEND` (the manifest sets `qwen-service`; the code default
is `chatterbox`), overridable per session via `POST /api/session/settings`
`{"backend": "..."}` against a hardcoded allow-list `{qwen, qwen-service, chatterbox}`.

`src/chatterbox_server.py` already exists, is resident, caches reference conditionals,
speaks the same file protocol, and supports `--model nano|turbo`. It has never run in the
cluster — only on `icarus` with MPS.

### TTS contract (must not change)

```text
POST /synthesize  {"text": str, "output": "/abs/path/out.wav",
                   "reference": "/abs/path/ref.wav" | "", "lang": "en", "frames": int}
               -> {"output": "/abs/path/out.wav", "generation_seconds": float, ...}
GET  /health   -> {"status": "ok", ...}
```

Paths, not uploads: the web pod and the TTS pod share the PVC, and the reference bytes
never travel over HTTP. Any new backend adapter must preserve this.

### Why this change is being considered

Qwen3-TTS **Base** (the variant in use) supports voice cloning but has *no* instruction or
emotion control — the official model table leaves the "Instruction Control" column blank
for `1.7B-Base`. Only `-VoiceDesign` and `-CustomVoice` have it, and neither clones. So
the current setup buys the Aussie accent and gives up expressiveness. Ed wants both.

Measured Qwen3-TTS cost on this node, for comparison: peak RSS 5817 MB, generation
65 ms/frame (~1.4× realtime overall), end-to-end 6.1 s for a 3.3 s reply and 10.4 s for a
5.9 s reply. See `docs/tts-sizing.md`.

---

## 2. Hard constraints

1. **Nothing runs on `icarus`** — it is holding a 47 GB LLM. No process, no build, no test.
2. **No `-march=native`** — the build host and the deployment node differ.
3. **Do not relax the `desktop-node` taint cluster-wide.** Tolerate it per-pod.
4. **Do not bake model weights into container images.** Images are pushed to a public
   registry; weights are downloaded at runtime into the hostPath cache. This is both a
   licence requirement (§11) and a size requirement.
5. **Keep pod resource requests modest.** This is the family desktop. TTS requests:
   CPU 2, memory 6Gi; limits CPU 10, memory 8Gi. Do not raise them to make a bigger model
   fit — that is a Phase 1 decision, not yours.
6. **Preserve the file protocol** from §1.
7. Do not commit unrelated changes, reformat files you are not changing, or use
   `git reset` / `git stash` / broad overwrites.

---

## 3. What is fixed, and what Phase 0 decides

**Fixed by this spec:** the protocol, the residency model (weights stay loaded), the
per-backend URL split (§6.1), the cue-policy plumbing (§6.2), the deployment shape, the
licence posture, and the rollback.

**Decided by evidence:** which model. Not by this document. The shortlist below is the
result of a literature and leaderboard review on 2026-11-14; treat it as the tested set,
not as a recommendation:

| # | Model | Size | Licence | Clone from ref | Per-turn emotion/style | Why it is on the list |
| --- | --- | --- | --- | --- | --- | --- |
| A | **Chatterbox Turbo** | 350M | MIT | yes upstream (`audio_prompt_path`) | 19 native tags `[chuckle]` etc. | Purpose-built for English voice agents; smallest; matches the cue policy already in the prompt |
| B | **Chatterbox Nano** | 110M | MIT | yes upstream | same tags | Claims 3× realtime on 8 CPU cores — would fix latency before any GPU |
| C | **CosyVoice 3** | ~0.5B | Apache-2.0 | yes + reference text | `instruct="Speak warmly"` + emotion cloning | Cleanest licence with real instruction control |
| D | **IndexTTS-2.5** | 0.8B | Apache + "must not be used to improve other AI models" | yes | 8-float emotion vector, decoupled from timbre | Most precise mood control; fits the pending 2080 |
| E | **Qwen3-TTS 1.7B (current)** | 1.7B | Apache-2.0 | yes | **none in Base** | Baseline to beat — you must generate it too |
| F | **Qwen3-TTS 1.7B-VoiceDesign** | 1.7B | Apache-2.0 | no (describes voice instead) | yes | Cheap zero-integration test of whether a described Australian voice is convincing |

**Explicitly rejected, do not test:** F5-TTS (weights CC-BY-NC, 16 GB full model,
Chinese-centric tokeniser, no emotion control) and XTTS-v2 (2023, CPML non-commercial,
unmaintained, flat prosody). They are what old blog posts recommend.

---

## 4. Phase 0 — A/B harness (measurement only, timebox 3 hours)

Goal: for each model in §3, produce WAVs and a latency number from **your own reference
clip on the production node**, and a blind listening set for Ed. No production changes.

### 4.1 Runtime

Use **`audio.cpp`** (`https://github.com/0xShug0/audio.cpp`) — one ggml binary hosting
`chatterbox`, `chatterbox_turbo`, `cosyvoice3`, `index_tts2`, and `qwen3_tts`, with a
model manager, CLI, and server. Using it for the harness avoids five separate Python
stacks and gives comparable timings.

- Pin a specific commit. Record it in the phase log.
- Build CPU-only for this phase (`docs/usage.md` at that commit, use its CPU build line
  verbatim — do not improvise flags). The pending 2080 needs `sm_75`; note the flag in
  §9 and do not attempt a CUDA build yet.
- Model installs: `python3 tools/model_manager_v2.py list` to discover exact package
  names, then `install <name>`. Record the exact names you installed.

Qwen3-TTS baseline (**E**): do not re-run it through audio.cpp. Copy the three existing
samples from `samples/cluster/` — they came from the production daemon and are the honest
baseline. If you want an apples-to-apples number, run `F` and note it separately.

### 4.2 Where to run it

Not on `icarus`, and not on your laptop — timings must come from the node. Use a throwaway
bench pod pinned to `august-desktop` with the taint toleration, model cache mounted from
the hostPath, audio and voices from the PVC. Template: **Appendix A**. Delete the pod when
Phase 0 ends, and verify with `pgrep -a audiocpp` on the node that nothing outlived it.

### 4.3 Corpus — use exactly these five lines

Store as `samples/ab-<YYYY-MM-DD>/corpus.tsv`, one line per row, tab-separated
(`id`, `text`):

```text
01	Oh, joy. Another riveting exchange awaits.
02	No, I'm not struggling. I'm just processing the sheer level of drama in that statement.
03	Well [chuckle], that went about as well as expected.
04	Yeah nah, she'll be right — we'll grab a servo pie and sort it out after the arvo.
05	You've been quiet for a bit. I'm not going to fill the silence just because it's there.
```

Rationale: `01` neutral short, `02` sarcasm (the persona's actual register), `03`
paralinguistic tag (expect models without tags to read or drop it — record which), `04`
Australian lexical/prosodic marker, `05` conversational timing. Line `03` is the crux for
models A and B.

Reference: `voices/clone/aussie-ref.wav`. **Also run `kajira-ref.wav`** for line `01` only,
so Ed can hear whether accent retention depends on the clip. See open question **O1**.

### 4.4 Commands and measurement

Per model, per line:

```sh
start=$(date +%s%N)
audiocpp_cli --task clon --family <family> --model <gguf> --backend cpu \
  --language en --text "$(cut -f2 ...)" --voice-ref /audio/voices/clone/aussie-ref.wav \
  --out "/audio/ab/<model>/<line>.wav"
end=$(date +%s%N)
```

Then `rtf = (end-start)/1e9 / audio_duration_seconds` (duration from
`ffprobe -show_entries format=duration` or `soxi`; if neither exists in the image, parse
the WAV header with a five-line Python snippet and record that you did).

Record into `samples/ab-<date>/metrics.csv`:

```csv
model,line,audio_seconds,wall_seconds,rtf,peak_rss_mb,notes
```

`peak_rss_mb` from `/proc/<pid>/status` `VmHWM` if the CLI exposes a PID, otherwise the
pod's `kubectl top` after the run, or omit with a note — **never invent it**.

### 4.5 Blind set for Ed

- WAVs for the listening test go in `samples/ab-<date>/listen/`, named
  `<code>-<line>.wav` where `<code>` is a random 3-letter string per model.
- `samples/ab-<date>/legend.txt` maps code → model + quant + licence, with the first line:
  `Listen first. Then read this.`
- Commit WAVs only if total size is under 25 MB; otherwise commit `metrics.csv`,
  `corpus.tsv`, `legend.txt`, and a `README.md` stating where the WAVs are on disk.

### 4.6 Phase 0 acceptance

- [ ] One WAV per (model, line) that the model could run, per §3 A–D and F.
- [ ] `metrics.csv` complete; `rtf` present for every row.
- [ ] Every failure recorded verbatim in `README.md` — "it did not run" is a valid and
      useful result, especially for D on CPU.
- [ ] `README.md` states: audio.cpp commit, exact model package names and quants, backend,
      pod spec used, and whether `[chuckle]` was rendered, read aloud, or dropped.
- [ ] Bench pod deleted; no stray processes on the node; models left in the hostPath cache
      (state their total size with `du -sh`).
- [ ] Nothing changed in `src/`, `web/`, the cluster, or the running deployment.

---

## 5. Phase 1 — the decision gate (Ed's, not yours)

Present him with: the blind set, `metrics.csv`, and one paragraph of plain summary. Then
stop and wait. Do not proceed on a guess.

His rubric (give him this text):

1. Does it still sound like the same Australian woman as the reference clip?
2. Is the sarcasm actually in the delivery, or just in the words?
3. Does the `[chuckle]` land as a chuckle?
4. Would you keep talking to it, or would the voice start to grate?

Tie-break rules, in order, if he is torn:

1. A model whose accent drifts fails, regardless of expressiveness.
2. If two are equally good by ear, take the smaller/faster one.
3. If nothing beats the Qwen3-TTS baseline by ear, **keep `qwen-service`** and go to
   Phase 3 §7.6 (the "no change" branch). That is a legitimate outcome, not a failure.
4. If the winner needs a GPU to be usable and the 2080 is not installed yet, record the
   choice, implement nothing, and mark Phase 2 blocked on hardware.

Record the outcome in **`docs/tts-decision.md`**: date, options tested, the four answers,
the chosen model + quant + licence, and the runner-up. One page, no hedging.

---

## 6. Phase 2 — productionise the winner

These four changes are required **whichever** model wins. Do them first, as their own
commit, because they are what makes the cutover reversible.

### 6.1 Per-backend URLs (currently one `TTS_URL` is shared — this is the blocking flaw)

`TTS_URL` is read by both the `chatterbox` and `qwen-service` branches, so two backends
cannot be co-resident and switching cannot be a single env change. Replace with
`TTS_URL_CHATTERBOX`, `TTS_URL_QWEN_SERVICE`, `TTS_URL_AUDIOCPP` (add as needed), each
falling back to the old `TTS_URL` for compatibility, then drop the fallback once the
manifests are updated. One commit, both repos, in step.

### 6.2 One source of truth for cue support

Today the "which backends may emit `[chuckle]`" policy is written three times: in
`RUNTIME_SYSTEM_PROMPT` (a prose sentence naming Chatterbox and Qwen3-TTS), in `chat()`
(`if session.backend == "chatterbox"`), and in `synthesize()` (same test). Replace with:

```python
BACKEND_CUES = {"chatterbox": True, "chatterbox-nano": True, "chatterbox-turbo": True,
                "audiocpp": False, "qwen": False, "qwen-service": False}
```

and build the prompt sentence from it, so a new backend cannot silently get the wrong
policy. The backend allow-list in `/api/session/settings` must also come from
`set(BACKEND_CUES)` rather than a literal. Add a unit test — see §6.5.

### 6.3 Implement the winner (exactly one path)

**Path A — Chatterbox (A or B wins).** Reuses code that already exists.

- `docker/Dockerfile.tts-chatterbox`: `python:3.12-slim`, CPU torch wheel from
  `https://download.pytorch.org/whl/cpu`, `pip install chatterbox-tts==<pinned>`, copy
  `src/`. Entrypoint runs `src/chatterbox_server.py --model <nano|turbo> --device cpu
  --host 0.0.0.0 --port 8190`. Do not download weights into the image.
- Weights: init container using the logic in `scripts/download-chatterbox-nano.sh`
  (`huggingface_hub.snapshot_download` to `/models/chatterbox-<model>`), hostPath cache.
- `deployment-tts-chatterbox.yaml` + Service on 8190, tolerations as in Appendix A,
  mounts mirroring the existing TTS deployment (`audio` and `voices` subPaths).
- Pin an immutable image tag; do not ship `:latest` for a new service.
- `WEB_DEFAULT_TTS_BACKEND=chatterbox`, `TTS_URL_CHATTERBOX` → the new Service.

**Path B — audio.cpp family (C or D wins).**

- Image builds `audiocpp_server` from the pinned commit; Python-free at runtime except
  the model manager.
- `audiocpp_server` speaks its own API, not ours. Add a small resident adapter
  (`src/audiocpp_adapter.py`, stdlib only, no torch) translating our file protocol to
  audio.cpp's routes, or add a `audiocpp` branch to `web_server.py` if audio.cpp's API is
  stable enough to call directly. Prefer the adapter: it keeps the contract in one place
  and lets you restart the model runtime without touching the web pod.
- Keep the reference clip cached by path+mtime (pattern already in
  `src/chatterbox_server.py`), and use audio.cpp's speaker/emotion cache slots so the
  reference is not re-encoded per turn.

**Path C — Qwen3-TTS wins (F, or the no-change branch).**

- Only if F beats everything: swap the GGUF in the existing `ttsd` deployment and adjust
  `entrypoint-tts.sh` flags. If nothing beats the baseline, implement nothing.

### 6.4 Docs and manifests

- `docs/architecture.md` still describes the `icarus`/Caddy deployment and is stale —
  update the diagram for the cluster while you are in there.
- Update `docs/deployment.md` env table for the new `TTS_URL_*` variables.
- Manifests go in `home-ops/clusters/dev/applications/edvox/`; commit and push (ArgoCD
  auto-syncs from git). If sync fights you, disable auto-sync on the app first, apply with
  `kubectl --context home-dev`, and re-enable — and say so in the report.

### 6.5 Tests

No test suite exists. Add `tests/test_backend_policy.py` using stdlib `unittest` only:
cue map covers every backend the allow-list accepts; prompt sentence is generated (not
hardcoded) and mentions tags for exactly the cue-capable backends; `synthesize` dispatch
resolves a URL for every backend. Run with `python3 -m unittest discover -s tests -v`.

---

## 7. Phase 3 — cutover and verification

### 7.1 Cutover

One commit: manifest env `WEB_DEFAULT_TTS_BACKEND=<winner>`. Push, wait for pods Ready.

### 7.2 Acceptance checks (all must pass)

```sh
kubectl --context home-dev get pods -n edvox            # all Ready, none restarted
kubectl --context home-dev logs -n edvox deploy/edvox-tts --tail=30   # weights loaded once, no per-request load
curl -s https://edvox.dev.initialed85.cc/api/health     # chat/stt/tts all "ok"
```

Then a real turn through the front end, and confirm each of these in the JSON response:

- [ ] `audio_url` returned, WAV non-zero and playable, 24 kHz or better.
- [ ] `timings.tts_ms` recorded — report it next to the Phase 0 `rtf`.
- [ ] A reply containing `[chuckle]` is **rendered**, not read aloud, not dropped
      (models A/B only). Send it as typed chat to force the tag through.
- [ ] Accent still Australian by ear on line `04`.
- [ ] `kubectl --context home-dev top pod -n edvox` memory under the limit after 5 turns;
      record peak.
- [ ] No OOMKill: `kubectl describe pod` shows `Last State: Running`.
- [ ] Whisper and web untouched (same image digests as before the change).

### 7.3 Record

Append a "Measured on the deployed service" table to `docs/tts-sizing.md`: model, quant,
node, threads/GPU layers, RTF, peak RSS, end-to-end for short and long turns. Measured,
not predicted.

### 7.4 Leave the loser running?

Only if it costs nothing (a second pod on the same node with the same PVC). If it does,
remove it — but keep the image published and the manifest in git so rollback is a revert,
not a rebuild.

### 7.5 Cleanup

Bench pods deleted, no stray `audiocpp`/`ttsd`/`chatterbox` processes on
`august-desktop` (`pgrep -a` each), model cache state reported, temporary branches
removed.

### 7.6 The "no change" branch

If Phase 1 kept `qwen-service`: still land §6.1 and §6.2 (they are improvements
regardless), keep `docs/tts-decision.md` with the rationale, and stop. The GPU work
(`CUDA` build for the pending 2080, `sm_75`) becomes its own future spec — do not start it
here.

---

## 8. Rollback

Revert one env value: `WEB_DEFAULT_TTS_BACKEND=qwen-service` (+ its `TTS_URL_*`), push,
wait for rollout. The old daemon and its model cache are untouched by design, so rollback
is seconds, not a rebuild. If the web image changed, `kubectl rollout undo
deploy/edvox-web`. Write the exact rollback commit hash into the Phase 3 report.

---

## 9. Open questions and known traps

**O1 — which reference clip is the Australian *female* voice?** Ed asked for an Australian
female accent. The three refs are `aussie-ref.wav`, `kajira-ref.wav`, `dainty-ref.wav`,
trimmed from `voice-aussie.wav`, `voice-kajira.wav`, `voice-dainty2.wav`. Provenance and
gender are undocumented. **Ask Ed before Phase 1.** If none is a female Australian voice,
the listening test cannot answer the question — say so and request a 10–20 s clip rather
than reporting a conclusion you cannot support.

**T1 — Chatterbox Turbo has no official GGUF.** `ggml-org/chatterbox-GGUF` currently ships
only the 0.5B original. `audio.cpp`'s Turbo port is marked *testing*, is built from a
third-party repack (`cstr/chatterbox-turbo-GGUF`, MIT-relicensed), and **does not support
custom voice cloning** — it only uses a built-in voice. If that is still true, Turbo cannot
be tested with your reference at all; record that, and test Nano/Turbo through
`chatterbox_server.py` (upstream cloning works: `model.generate(text,
audio_prompt_path=ref)`) instead. That means a small Python harness in the bench pod —
acceptable, keep it in `scripts/`, not in `src/`.

**T2 — Chatterbox is retired.** Upstream archived it in Aug 2026; MIT, no maintenance. Fine
for personal use; note it in `docs/tts-decision.md` as an accepted risk, and do not plan a
fork on top of it.

**T3 — `llama.cpp` now hosts Chatterbox too** (0.9.0, Nov 2026, `llama-tts` +
`--tts-speaker-file`). The current image pins an older commit. Do not upgrade the pinned
commit during Phase 0/2 — it is a separate change with its own risk, and `ttsd` was written
against the current TTS API. Log it in §9 as a follow-up.

**T4 — the 2080 is `sm_75`.** Turing; a CUDA 12.x build must include `75`, and the
driver on `august-desktop` must be new enough. Not this spec's work.

**T5 — CPU is the ceiling, not RAM.** Anything ≥1B will be ~1.5× realtime or worse here.
If Phase 0 shows every candidate slower than the current baseline, that is a finding, not a
bug: write it down and let Ed decide whether to wait for the GPU.

---

## 10. Report template

```text
Phase reached: 0 | 1 | 2 | 3 | blocked at <where>
Winner (or "no change"): <model + quant + licence>
Measured: <rtf per model, peak RSS, end-to-end short/long>
Changed: <files, commits, image tags, manifests>
Verified: <acceptance checks, pass/fail each>
Rollback: <commit hash / env value>
Left running: <pods, processes, and why>
Open: <O1 answer, T1/T3 status, anything Ed must decide>
```

---

## 11. Licence ledger (fill in as you go)

| Model | Weights licence | Commercial OK | Redistribution |
| --- | --- | --- | --- |
| Qwen3-TTS (all variants) | Apache-2.0 | yes | yes, but not baked into our image |
| Chatterbox Nano/Turbo/0.5B | MIT | yes | same |
| CosyVoice 3 | Apache-2.0 | yes | same |
| IndexTTS-2/2.5 | Apache-2.0 + "must not be used to improve other AI models" | personal use fine; flag if ever productised | same |
| Fish Drama 3 | Fish Audio Community | **check before any use beyond personal** | no |
| F5-TTS weights | CC-BY-NC | no | no |

No weights in images, ever — that keeps every row above a non-issue for the registry.

---

## Appendix A — bench pod template

Copy verbatim; change only the container command and the mounts you need.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: ttsab
  namespace: edvox
spec:
  nodeSelector:
    kubernetes.io/hostname: august-desktop
  tolerations:
    - key: desktop-node
      operator: Equal
      value: "true"
      effect: NoSchedule
  restartPolicy: Never
  activeDeadlineSeconds: 7200
  volumes:
    - name: models
      hostPath:
        path: /home/edward/.cache/edvox/models
        type: Directory
    - name: data
      persistentVolumeClaim:
        claimName: edvox-data
  containers:
    - name: ab
      image: <your audiocpp image, pinned tag>
      command: ["/bin/sh", "-c", "sleep 7000"]
      resources:
        requests: { cpu: "2", memory: 2Gi }
        limits:   { cpu: "8", memory: 8Gi }
      volumeMounts:
        - { name: models, mountPath: /models }
        - { name: data, mountPath: /audio,  subPath: audio }
        - { name: data, mountPath: /audio/voices, subPath: voices }
```

Delete it when done: `kubectl --context home-dev delete pod ttsab -n edvox`.

## Appendix B — before you start

Read, in this order: `AGENTS.md`, `docs/architecture.md`, `docs/deployment.md`,
`docs/tts-sizing.md`, then `src/web_server.py` (search `CHATTERBOX_CUE_RE`,
`def synthesize`, `BACKEND`), `src/chatterbox_server.py`, `docker/entrypoint-tts.sh`.
Check `git status` and do not disturb uncommitted work.
