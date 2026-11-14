# Phase 0 results — TTS candidate measurements

Executed 2026-11-14 against `docs/tts-changeover-spec.md`. All numbers are from
`august-desktop` (Ryzen 5 5600G, 6C/12T, 30 GB RAM, 8 threads per process, CPU only — the
RTX 2080 is not installed yet). Nothing here is a recommendation to cut over; it is the
evidence for the Phase 1 listening decision, which is Ed's.

**Audio never entered git.** Renders live on the PVC at `/audio/ab/` and in the local
working tree at `samples/ab/` (`*.wav` is gitignored). These are clones of a real,
identifiable person in a public repository.

## Headline

**Only one candidate does the thing we are changing over for: upstream Chatterbox Turbo in
Python — real paralinguistic control *and* Australian voice cloning, running on this CPU.**

Everything else fails a hard requirement:

| candidate | runtime | RTF (mean / best) | peak RSS | clones our voice? | emotion control | verdict |
|---|---|---|---|---|---|---|
| **Qwen3-TTS 1.7B Base (production)** | C++ `ttsd`, resident | **1.43** | 5 817 MB | yes | **none** | fastest, but no control at all |
| **Chatterbox Turbo — upstream Python** | torch CPU, resident | **2.77** | 6 386 MB | yes | **19 tag tokens + `exaggeration`** | **only viable expressive option tonight** |
| Chatterbox 0.5B (GGUF) | audio.cpp | 5.22–5.96 | 2 524 MB | yes | tags spoken as literal words | out on control |
| CosyVoice3 (GGUF) | audio.cpp | 6.80 (30.35 contended) | 2 719 MB | yes | `instruct` template — works | out on speed/verbosity |
| Chatterbox Turbo (GGUF) | audio.cpp | 4.05 | 3 891 MB | **no** (`supports_speaker_reference 0`) | none | out — cannot keep the accent |
| IndexTTS-2.5 (GGUF) | audio.cpp | 13.1–18.3 | 4 279 MB | yes | `--emotion` text + alpha | out on speed; server died after 1 line |
| Chatterbox Nano | upstream Python 0.1.7 | not measurable | — | — | — | out — no loader in 0.1.7 (see T1) |

RTF is seconds of compute per second of audio. **Both the current production model and the
leading candidate are above 1.0**, i.e. sub-real-time on CPU; edvox lives with it today
because replies are short.

## The tag finding (the whole decision turns on this)

Spec §9 trap T1 said audio.cpp's Chatterbox integration might not support cloning. It is
the opposite, and worse for us: **the GGUF conversion keeps cloning but throws the
paralinguistic vocabulary away.** Every audio.cpp family rendered `[chuckle]` as speech:

- `chatterbox_q8_0` → whisper hears the word "chuckle"
- `index_tts2_5` → whisper hears "chuckly" (mis-articulated word)
- `cosyvoice3` → dropped the leading "Well," and spoke "chuckle"

`chatterbox-tts==0.1.7`. The upstream Python runtime still has the tokens. `added_tokens.json`
in both checkpoints carries 19 of them — `[advertisement]`, `[angry]`, `[chuckle]`,
`[clear throat]`, `[cough]`, `[crying]`, `[dramatic]`, `[fear]`, `[gasp]`, `[groan]`,
`[happy]`, `[laugh]`, `[narration]`, `[sarcastic]`, `[shush]`, `[sigh]`, `[sniff]`,
`[surprised]`, `[whispering]` — and the tokenizer resolves `[chuckle]` to a single special
id (50274), not to wordpieces.

Ablation on identical text (`scripts/ab_tag_ablation.py`, `scripts/make_blind_set.py`
renders the rest), reference `aussie-conv-ref.wav`:

| render | text fed in | whisper transcription | duration |
|---|---|---|---|
| stripped | `Well, that went about as well as expected.` | "Well, that went about as well as expected." | 2.32 s |
| **tagged** | `Well [chuckle], that went about as well as expected.` | "Well, that one's about as well as expected." | 3.00 s |
| word | `Well chuckle, that went about as well as expected.` | "Well **chuckle.** That went about as well as expected." | 3.04 s |

In the tagged render "chuckle" is **absent from the transcript** and whisper garbles the
adjacent words — the signature of a non-verbal vocalisation displacing speech, not a word
being read. The word render puts "chuckle" squarely in the transcript. Duration alone cannot
tell them apart (0.68 s vs 0.72 s added), which is why the transcription test is the
discriminator. Do not trust my acoustic aligner (`scripts/tagdiff.py`): its flatness numbers
separate the two cases inconsistently and it is not a sound basis for a decision. Your ears
are the instrument here, which is what Phase 1 is for.

Prosody responds to the tags measurably, on the *same* text and *same* reference:

| render | median F0 | p10–p90 | pitch spread |
|---|---|---|---|
| plain line 01 | 212.4 Hz | 195–249 | 53 Hz |
| `[sarcastic]` line 01 | 263.7 Hz | 188–296 | 109 Hz |
| `[laugh]` line 01 | 240.0 Hz | 186–282 | 96 Hz |
| reference clip | 224.3 Hz | 180–267 | 86 Hz |

CosyVoice3's `instruct` template does the same thing by another route, on the identical
line-01 text and reference: plain zero-shot median 228.6 Hz / spread 62 Hz, versus
`instruct="Speak with warm amusement, like you are teasing a friend."` at median 266.7 Hz /
spread 135 Hz. So instruction control is genuinely present in both surviving families; it is
the *accent* and the *delivery* that need listening.

## Corrections to the spec and to our own code

1. **T1 confirmed and inverted.** `from_local()` in `chatterbox-tts==0.1.7` is
   `from_local(ckpt_dir, device)` — there is **no `nano` argument**. Our
   `src/chatterbox_tts.py` passes `nano=...` and would `TypeError` the moment it ran. Fixed.
   Nano vs Turbo is selected by *which checkpoint directory* you point at.
2. **Chatterbox Nano is not loadable at all with 0.1.7.** `ChatterboxTurboTTS` hardcodes
   the turbo hyperparameters and loads `t3_turbo_v1.safetensors`; the nano repo ships
   `t3_nano_v1.safetensors`, and 1 of 155 nano tensors shape-matches turbo (217.5 M vs
   478.9 M params). Getting nano running means either upstream source changes or a newer
   release than 0.1.7. It is moot for now: Turbo is both faster-qualifying and better.
3. **`supports_style_condition 1` in `audiocpp_cli --log` is misleading.** It means the
   family has *some* separate style/emotion input (CosyVoice3's `instruct`, IndexTTS's
   `--emotion`), **not** that inline `[tags]` work. Capability flags are a starting point for
   probes, never a conclusion.
4. **CosyVoice3 needs two things no doc told us about** (this cost three failed runs):
   a **16 kHz** prompt clip — with the 24 kHz reference it emitted 0.38–1.2 s of truncated
   garbage per line — and a **verbatim transcript** of that clip via `--reference-text`. A
   trimmed transcript made it drop the first words of the target text. `scripts/download`
   aside, keep `ref16k.wav` alongside every CosyVoice3 reference.
5. **`--language` is not universal.** CosyVoice3 rejects it outright; harnesses must vary
   flags per family rather than assume one command shape.
6. **Clipping is real but not monotonic in `exaggeration`.** Measured peak per render:
   stripped 0.493, `exaggeration=0.3` 0.750, `0.8` **1.005 (over full scale)**, word 0.943,
   `1.5` 0.765. The loudness dial does not map monotonically onto peak amplitude, and
   `norm_loudness=True` (already the default in `generate()`) does not guarantee peak safety
   either. Phase 2 should therefore **not** clamp the dial to a conservative ceiling; it
   should add an explicit peak guard (scale to ≈‑1 dBFS) so any `exaggeration` the listener
   likes stays usable. Ed's ear picked `1.5` as the good breathy chuckle and it is the
   *clean* one — clamping at 0.7 would have removed the sound actually wanted while still
   admitting the 0.8 render that clipped.
7. **Multi-model co-residency works but is not free.** One `audiocpp_server` held
   Chatterbox + CosyVoice3 + IndexTTS (7.9 GB of GGUFs) loaded simultaneously and served
   `/v1/audio/speech` against `--voice-dir`, auto-discovering `aussie-conv-ref` and
   `aussie-expressive-ref`. Under that contention CosyVoice3's mean RTF went 6.80 → 30.35
   and IndexTTS's server stopped responding after one line. Two voices in one process is
   viable; three on this node is not. Note the server needs models declared in a
   `--config` JSON (`id`, `family`, `path`) — it does not scan `--models-root` for them.
8. **edvox service port**: `edvox-tts` is **8191**, not 8080, and `ttsd` will only write
   under `TTS_ALLOW_ROOTS=/app/edvox/audio:/app/edvox/voices`. That PVC `audio` subPath is
   what the bench pod sees as `/audio`, which is how the Qwen baseline renders were produced.

## Method and reproducibility

- Corpus: spec §4.3's five lines, verbatim (`/audio/ab/corpus.tsv`), including the Aussie
  idiom line and the tag line.
- Reference: `aussie-conv-ref.wav` as primary for every cloning candidate, with
  `aussie-expressive-ref.wav` as the line-01 probe. The caricature clip and the two
  non-Australian clips were not used.
- Qwen baseline rendered **through the live production daemon** on the same node, same
  texts, same reference — so it is an honest comparison, not a remembered number.
- Harnesses: `scripts/run_ab.py` (audio.cpp CLI, per-request), `scripts/run_resident.py`
  (audio.cpp server, steady state), `scripts/ab_chatterbox.py` and
  `scripts/ab_tag_ablation.py` (upstream Python, model loaded once → generation-only times),
  `scripts/make_blind_set.py` (24-clip randomised listening set + `KEY.txt`),
  `scripts/f0set.py` (coarse timbre check; median/p10/p90 F0 by 40 ms autocorrelation,
  voiced only — every F0 figure in this document uses that one method so the rows are
  comparable).
- Image: `ghcr.io/0xshug0/audio.cpp:full-cpu-20261114-69dcc51` for the GGUF families;
  `docker/Dockerfile.bench-chatterbox` → `initialed85/edvox-bench-chatterbox:0.1.7` for
  upstream Chatterbox. `chatterbox-tts==0.1.7` re-resolves torch to the **CUDA** build even
  when the CPU wheel is installed first, which is why that image is 12.1 GB; it runs
  correctly on CPU, and it is transferred to the node with
  `docker save | ssh node 'sudo -n docker load'` rather than pushed.
- Model weights: `model_manager_v2.py` packages `chatterbox_q8_0`, `chatterbox_turbo_q8_0`,
  `cosyvoice3_q8_0`, `index_tts2_5_q8_0`; `huggingface_hub.snapshot_download` for
  `ResembleAI/chatterbox-nano` and `ResembleAI/chatterbox-turbo`.

Known measurement caveats, stated plainly: `run_resident.py`'s TTFB column is unusable
(curl's output file became visible before bytes landed, so several readings are 0.00), so
**streaming first-audio latency is unmeasured** — it matters a lot for how edvox will feel
and belongs in Phase 2. Python timings are generation-only from a resident model; audio.cpp
CLI numbers are per-request but include only ~1–2 s of setup, and were confirmed against the
resident server (5.96 mean vs 5.22), so the two sets are comparable. `f0set.py` measures
pitch, which is a timbre sanity check and *not* an accent test.

## What to listen to (Phase 1 — this is your decision, not mine)

Local: `samples/ab/listen/` — 24 clips, four texts (sarcasm, the tag line, the Aussie-idiom
line, the silence line) across six voices, randomised labels with `KEY.txt` alongside.
`samples/ab/tag/` is the unblinded set: the `[chuckle]` ablation, `exaggeration` ladder,
CosyVoice3 `instruct` vs zero-shot, and IndexTTS `--emotion`.

Three questions the numbers cannot answer:

1. **Does Turbo's cloned Australian sound right to you?** Line 04 (`she'll be right …
   servo pie … arvo`) is the accent stress test. Turbo-GGUF cannot clone and should sound
   like a different person — use it as the null.
2. **Is the `[chuckle]` render a laugh, or a garble?** Compare
   `chatterbox_turbo_upstream-03-tagged.wav` against `-03-stripped.wav` and `-03-word.wav`.
3. ~~How much `exaggeration` is too much?~~ **Answered: 1.5** is the good breathy chuckle,
   and it does not clip. See correction 6 — the dial gets a peak guard, not a clamp.

### Phase 1 — how the picks developed

The first two selections cross runtimes. `04-G07` ("good accent-wise") is **Chatterbox
GGUF**, the runtime that renders `[chuckle]` as the literal word; `03-tagged-x15` ("good
breathy laugh chuckle thing") is **Chatterbox Turbo under upstream Python**, the only
runtime that still has the tag vocabulary. The accent that passed and the laugh that passed
are not currently available from the same server.

### Phase 1 final: the vocabulary and the accent are coupled, and there is no sweet spot

The third candidate runtime was `chatterbox.mtl_tts.ChatterboxMultilingualTTS` — the standard
repo's second tokenizer (`mtl_tokenizer.json`) plus `t3_mtl23ls_v3.safetensors`, carrying 31
non-verbal events (`[giggle]`, `[laughter]`, `[guffaw]`, `[sigh]`, `[whisper]`, `[gasp]`,
`[mumble]`, `[clear_throat]`, `[humming]`, `[singing]`, ...) and an `exaggeration` dial. Its
tokens render correctly as non-speech — none was read as a word, and the `[giggle]` render
transcribed as the clean sentence with the sound absorbed into the audio.

It was rejected by ear: **no Australian accent coming through**. It also cost RTF 5.36–7.94
(19–37 s per line) and mangled content — `Well [laughter], that went…` transcribed as
"Well, **Turo**, that went…", `[sigh] I suppose that is fine` as "**Datto**, I suppose that
is fine".

Taken together the three listening verdicts form a pattern worth stating plainly, because it
predicts future results and it is not a runtime artefact:

| checkpoint | non-verbal vocabulary | accent verdict (Ed) | RTF |
|---|---|---|---|
| Chatterbox standard base (GGUF / Python) | **none** | *"really nails it"* | 5.2 / 5.4–9.0 |
| Chatterbox MTL 23-lang | 31 tokens, renders as non-speech | *"basically no Australian accent"* | 5.4–7.9 |
| Chatterbox Turbo | 19 tokens, renders as non-speech | *"marginally more British"*; American with the expressive ref | **2.2–2.6** |

**Every checkpoint that gained the paralinguistic vocabulary lost the accent, and every
checkpoint that keeps the accent has no vocabulary.** Same reference clip, same texts, same
node throughout — so conditioning material is not the variable and the reference window is
not the lever. The likely mechanism is that the tag-capable models were trained on a wider,
mostly non-Australian mixture, and reference-conditioning is too weak to hold an accent
against that prior. Implication for planning: **the RTX 2080 will not fix this.** GPU buys
speed, not accent. Closing the gap needs a model that was trained to take paralinguistic
tags *and* conditions hard on the speaker — i.e. fine-tuning or a purpose-built dataset, not
another checkpoint from this family.

The practical choice is therefore a straight trade, not a search: Turbo buys expressiveness
and the only usable CPU speed at the cost of a near-miss accent; standard GGUF buys the
accent at 2× the latency and no non-verbal sounds.

One accent candidate has still never been judged by ear: **CosyVoice3** (`04-V20`). It is
slow (RTF 6.8) and was verbose and lossy on line 03, but its control mechanism is an
`instruct` free-text directive rather than a special-token vocabulary — the only surviving
candidate where expressiveness does not come from the training mixture that appears to cost
the accent. It was eliminated on performance before listening, which was a measurement
ordering decision, not evidence about its accent.

## If you pick Chatterbox Turbo (Phase 2 shape)

- Replace or parallel the C++ `ttsd` with a resident Python service holding
  `ChatterboxTurboTTS` loaded once (6 s cold, 6.4 GB resident). This is the real cost: it
  moves us off the C++ daemon and adds a torch runtime to the image.
- The spec's prerequisites still stand and get easier: split `TTS_URL` into per-backend
  URLs, and move cue policy into one `BACKEND_CUES` map — now knowable per backend rather
  than guessed.
- Clamp `exaggeration` to a non-clipping ceiling and add headroom normalisation.
- Expect ~2× slower than Qwen. **Keep Qwen resident as the low-latency voice** and route
  short/urgent replies to it if the slowness bites — co-residency is proven at two models.
- Re-run `scripts/run_ab.py` and `scripts/run_resident.py` after the RTX 2080 lands with
  `--backend cuda`; GPU changes the ranking more than any tuning on CPU will.

## Cleanup state

Bench pods `ttsab` and `ttsabcb` and all bench Deployment/Job objects were removed after
these artefacts were saved. Production `edvox-web`, `edvox-tts`, `edvox-whisper` were left
running and healthy throughout; the only writes to production state were Qwen renders
through the existing `/synthesize` contract and the `voices/clone/` clips. GGUF weights
(~9 GB) and Chatterbox checkpoints (~3.6 GB) remain on the node's models hostPath for
Phase 2 reuse; delete `/home/edward/.cache/edvox/models/audio.cpp` and
`models/chatterbox-*` if disk is wanted back. `/models/ab-qwen` was left empty.
