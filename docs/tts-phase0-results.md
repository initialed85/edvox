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
| **Chatterbox Turbo — upstream Python** | torch CPU, resident | **2.77** | 6 386 MB | yes | **19 tag tokens** (`exaggeration` is accepted but inert — see correction below) | **only viable expressive option tonight** |
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
6. **Clipping is real but is not controlled by `exaggeration` at all.** Measured peak per render:
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
3. ~~How much `exaggeration` is too much?~~ **Unanswerable: the dial is inert on Turbo**
   (bit-identical renders at 0.0/0.5/1.5/2.0 under a fixed seed — see the correction below).
   Every earlier "1.5 is better" listening note was a comparison of two draws from the same
   distribution.
4. **The question that replaced it: is `temperature=0.5` still expressive?** Turbo's word
   accuracy at 0.8 was 2/6 on the idiom line and 4/4 at 0.5, so 0.5 is proposed — but a
   calmer sampler may also flatten the delivery we chose this model for. Listen to
   `samples/ab/final/FINAL-*-temp05.wav` and say whether the laugh and the accent still land
   at the setting that can be shipped.

### Phase 1 — how the picks developed

The first two selections cross runtimes. `04-G07` ("good accent-wise") is **Chatterbox
GGUF**, the runtime that renders `[chuckle]` as the literal word; `03-tagged-x15` ("good
breathy laugh chuckle thing") is **Chatterbox Turbo under upstream Python**, the only
runtime that still has the tag vocabulary. The accent that passed and the laugh that passed
are not currently available from the same server.

### Phase 1 interim read: every tag-capable checkpoint lost the accent

*(Superseded in part by the section below — `exaggeration` turned out to confound the Turbo
verdict. The MTL result stands: it was tested at 1.5 as well and had no accent either way.)*

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
checkpoint that keeps the accent has no vocabulary.** (This reading was formed before the
`exaggeration` confound was found — see below.) Same reference clip, same texts, same
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

### This was later revised: `exaggeration` was confounding the accent verdict

> **Superseded by the next section.** The mechanism proposed here does not exist on Turbo.
> Read the correction below before relying on any `exaggeration` number in this section.

The "marginally more British" and "basically standard American" verdicts on the idiom line
were heard at the library default `exaggeration=0.5`. Re-rendering line 04 at Ed's chosen
`1.5` with the same reference and the tag present (`scripts/turbo_preflight.py`,
`samples/ab/preflight/04-idiom-x15-tagged.wav`) passes on both counts — full idiom intact
*and* the tag absorbed as non-speech:

| render | exaggeration | whisper transcription | tag heard as word? |
|---|---|---|---|
| line 04 plain | 0.5 | "Yeah, now she'll be right. We'll grab a **sapphire pie** and sort it out after the **art vote**." | n/a |
| line 04 plain | 1.5 | "Yeah, no, sure be right. We'll grab a **servo pie** and sort it out after the **arvo**." | n/a |
| **line 04 + `[chuckle]`** | **1.5** | "Yeah, nah, should be right. We'll grab a **servo pie** and sort it out after the **arvo**." | **no** |
| line 07 + `[chuckle]` | 1.5 | "So I let me catch that, could you set rails?" | no, but garbled |

Counter-intuitively, articulation is **better** at 1.5 than at the 0.5 default: the low
setting mangled "servo pie" into "sapphire pie" and "arvo" into "art vote", which is what a
flattened delivery sounds like to the STT. So the accent problem was at least partly a
delivery-setting problem, and the trade framed above is narrower than it looked — Turbo at
`exaggeration=1.5` gets the idiom *and* the non-verbal sound, on the only candidate that runs
at usable CPU speed (RTF 1.96–2.52).

Not everything holds: `07-plain-x15-tagged` garbled a short sentence that rendered cleanly
without the tag, so tag placement on brief replies is unreliable and needs a policy rather
than blanket use. Peaks in this batch stayed ≤ 0.918 at `1.5`, but 1.015 was measured
earlier at the same setting, so the peak guard remains mandatory.

One accent candidate has still never been judged by ear: **CosyVoice3** (`04-V20`). It is
slow (RTF 6.8) and was verbose and lossy on line 03, but its control mechanism is an
`instruct` free-text directive rather than a special-token vocabulary — the only surviving
candidate where expressiveness does not come from the training mixture that appears to cost
the accent. It was eliminated on performance before listening, which was a measurement
ordering decision, not evidence about its accent.

### Correction, again, and this time it is structural: `exaggeration` does nothing on Turbo

The revision above attributed the "sapphire pie" → "servo pie" improvement to the dial. That
attribution is wrong, and the evidence is not a listening judgement:

```python
m.prepare_conditionals(ref, exaggeration=e)
m.generate(text)          # fixed torch/numpy seed, same reference, same text
```

| exaggeration | output vs exaggeration=0.0 |
|---|---|
| 0.0 | (baseline) |
| 0.5 | **bit-identical** |
| 1.5 | **bit-identical** |
| 2.0 | **bit-identical** |

All four renders were the same 42,240 samples. The reason is structural: `prepare_conditionals`
bakes the value in as `T3Cond.emotion_adv`, which is consumed only by `cond_enc.emotion_adv_fc`
(a `Linear(1, n_channels)` instantiated when `hp.emotion_adv` is set). Turbo's
`t3_turbo_v1.safetensors` has **no `emotion_adv_*` key among its 299 tensors**, and in the loaded
model `t3.cond_enc.emotion_adv_fc is None`. The value is written into the conditional and then
read by nothing. Upstream already says so — `generate()` warns "CFG, min_p and exaggeration are
not supported by Turbo version and will be ignored" — and that warning is accurate; the base
class `tts.py` has a branch that patches `emotion_adv` into cached conditionals, Turbo does not.

Consequences, in order of importance:

1. **The `0.5` vs `1.5` comparison was two draws from the same distribution.** So was every
   other `exaggeration` row in this document, including the `preflight` table above. The idiom
   line is a coin flip, not a dial setting.
2. **The lever that actually moves Turbo is `temperature`**, via `t3.inference_turbo`. Measured
   on the idiom line (`servo pie` and `arvo` both correct in the live whisper transcript),
   6 draws at the library default and 4 each at lower settings, same reference, same resident
   service (`src/chatterbox_server.py` over HTTP):

   | temperature | idiom correct | wall per turn |
   |---|---|---|
   | 0.8 (upstream default) | **2 / 6** | 8.7–9.9 s |
   | 0.5 | **4 / 4** | 9.9–13.3 s |
   | 0.3 | **4 / 4** | 10.3–22.9 s |

   Small n, but 2/6 versus 4/4 twice is not subtle. At 0.8 a mangled turn is the norm, not the
   exception: one draw rendered "she'll be right" as "shall we write", another dropped "Yeah
   nah" entirely. Lower temperature also costs speed at 0.3 (one draw took 22.9 s), so 0.5 is
   the setting to ship unless listening says otherwise.
3. **`[chuckle]` is still absorbed as non-speech at temperature 0.5** (transcripts contain no
   spoken word for it), but its *duration* signature is unreliable — plain-vs-tagged deltas at
   0.5 were +3.04 s, -0.72 s, +0.48 s. Duration difference is therefore not a good automated
   tag detector; transcription absence is.
4. Phase 0/1 accent verdicts that compared Turbo renders are **noise-dominated** and should be
   read as "unresolved at n=1" rather than as findings. The coupling conclusion still holds for
   MTL/standard, which were judged on clearly different outputs, and the GGUF-vs-Turbo accent
   gap was heard across several lines rather than one.

The `exaggeration` parameter stays in the service contract — it is genuinely honoured by the
standard and MTL checkpoints, and a caller should not change wire formats when it changes
models — but the service now reports `exaggeration_is_inert_on_this_model` in `/health` and
`dial_applies_to_model` per response so nobody is fooled by it a third time.

## If you pick Chatterbox Turbo (Phase 2 shape) — now implemented

- Replace or parallel the C++ `ttsd` with a resident Python service holding
  `ChatterboxTurboTTS` loaded once (6 s cold, 6.4 GB resident). This is the real cost: it
  moves us off the C++ daemon and adds a torch runtime to the image.
- The spec's prerequisites still stand and get easier: split `TTS_URL` into per-backend
  URLs, and move cue policy into one `BACKEND_CUES` map — now knowable per backend rather
  than guessed.
- ~~Clamp `exaggeration` to a non-clipping ceiling~~ — the dial is inert on Turbo, so it
  cannot be the clipping control. Instead: `norm_loudness=True` equalises loudness without
  bounding amplitude, so the service applies a **peak guard** (scale to ~-1 dBFS when
  `raw_peak` > 0.988) and reports `raw_peak` / `peak_guard_applied` per response.
- Ship **`temperature=0.5`**, not the upstream 0.8. At 0.8 the idiom line survived 2/6 turns;
  at 0.5, 4/4. This is the single highest-impact setting in the whole changeover.
- Expect ~2× slower than Qwen. **Keep Qwen resident as the low-latency voice** and route
  short/urgent replies to it if the slowness bites — co-residency is proven at two models.
- Re-run `scripts/run_ab.py` and `scripts/run_resident.py` after the RTX 2080 lands with
  `--backend cuda`; GPU changes the ranking more than any tuning on CPU will.

## What Phase 2 actually shipped

- `src/web_server.py`: `BACKEND_CUES` is the one cue-policy source, the system-prompt
  paragraph is generated from it, `normalize_backend()` gates the settings route, and
  `tts_url_for()` gives every backend its own variable (`TTS_URL_CHATTERBOX`,
  `TTS_URL_QWEN_SERVICE`, `TTS_URL_AUDIOCPP`) with a legacy `TTS_URL` fallback. `/health`
  now reports `tts_backend` plus a `tts_backends` map instead of one hardcoded port.
- `src/chatterbox_server.py`: rewritten against the real 0.1.7 API (`from_local(ckpt_dir,
  device)`; no `nano=` kwarg), conditionals cached by `(path, mtime_ns, size,
  exaggeration)`, 16-bit PCM output, `TTS_ALLOW_ROOTS` confinement matching
  `native/ttsd.cpp`, peak guard, startup warm-up, and `temperature` / `repetition_penalty`
  exposed per request.
- `tests/test_backend_policy.py`: 15 tests asserting the *relationships* — that the prompt
  prose is generated from the map, that the settings allow-list and the dispatch share one
  source, and that backends resolve to distinct URLs. These fail if a backend is added
  without a measured cue policy.
- Deployed on `home-dev` as `edvox-tts-chatterbox` (image
  `initialed85/edvox-tts-chatterbox:0.1.7`, weights fetched idempotently by an init
  container) with `edvox-web` still defaulting to `qwen-service`, so the cutover is one env
  var (`WEB_DEFAULT_TTS_BACKEND`) and rollback is the same var flipped back. Cutover is held
  for the listening decision. Two things that only the live path caught: a session with no
  uploaded voice sends `reference:""`, which Qwen answers from its own speaker list and
  Chatterbox 500'd on (now `CHATTERBOX_DEFAULT_REFERENCE`); and `/health` inherited the
  legacy `TTS_URL` for every backend, so it reported `audiocpp: ok` against a service that
  does not exist.
- Verified live over HTTP on CPU: model load 5.9 s, reference warm-up 11.6 s, ~8.7–13.3 s per
  5–6 s turn at temperature 0.5, `PCM_S` 24 kHz, cache hits reported.

## Cleanup state

Bench pods `ttsab` and `ttsabcb` and all bench Deployment/Job objects were removed after
these artefacts were saved. Production `edvox-web`, `edvox-tts`, `edvox-whisper` were left
running and healthy throughout; the only writes to production state were Qwen renders
through the existing `/synthesize` contract and the `voices/clone/` clips. GGUF weights
(~9 GB) and Chatterbox checkpoints (~3.6 GB) remain on the node's models hostPath for
Phase 2 reuse; delete `/home/edward/.cache/edvox/models/audio.cpp` and
`models/chatterbox-*` if disk is wanted back. `/models/ab-qwen` was left empty.
