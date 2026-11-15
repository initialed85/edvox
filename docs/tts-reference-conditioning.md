# Why Chatterbox started talking about Tamworth

Observed 2026-11-15 on `home-dev`: several spoken answers were not the text the LLM wrote. They
were the reference clip's own content.

| assistant text that was meant to be spoken | what actually came out of the speaker |
|---|---|
| "Back again. Did you manage to accomplish anything interesting while you were gone?" | "…originally from a little city called Tamworth…" |
| "Surviving, mostly. You know how it is. What's the latest drama in your corner of the internet?" | "So I live in Sydney in New South Wales, but I am originally from… Tamwa…" |
| "I am here to assist you. Can you tell me what is on your mind?" | "I am originally Sydney in the South Wales, but I am originally from Tamworth" |

Three of Ed's five turns in one session. The text is in `logs/llm/*.jsonl`; the audio is in
`audio/web/*/*response*.wav`; the transcripts above came from re-listening to those files through
`edvox-whisper`. The sentences are the content of `voices/clone/aussie-conv-ref.wav` — Amy's
reference clip opens by describing where she grew up.

## The mechanism

`chatterbox.tts_turbo.ChatterboxTurboTTS.prepare_conditionals()` does three things with the
reference clip, and only one of them is what people assume:

1. **Voice-encoder embedding** — `ve.embeds_from_wavs([ref_16k_wav], sample_rate=S3_SR)`. Who the
   voice is. Full clip.
2. **Voice-decoder reference** — `s3gen_ref_wav[:DEC_COND_LEN]`, `DEC_COND_LEN = 240000` samples
   at 24 kHz = 10 s. How the generated audio is coloured.
3. **Language-model prefix** — `s3_tokenizer.forward([ref_16k_wav[:ENC_COND_LEN]], max_len=plen)`
   where `plen = t3.hp.speech_cond_prompt_len = 375` on the turbo checkpoint. **This is speech
   tokens from the start of the reference clip, used as the prefix the T3 language model continues
   from.** 375 tokens is about 7.5 s of audio.

Turbo therefore does not merely *clone* a voice from the reference; it continues a *sequence* that
begins inside the reference. Give it a line with weak semantic anchor — a return greeting, a
phatic "surviving, mostly", a stock "I am here to assist you" — and the continuation it prefers is
the one already sitting in the prefix: a fluent first-person monologue about where this person
lives. A well-anchored sentence ("the servo pie run took about forty minutes this arvo") is strong
enough to override the prefix, which is why this looked intermittent rather than constant.

## What is not the lever

- **Temperature.** The two lines that leaked for Ed leak at `temperature=0.5` *and* at `0.3`, with
  the same content. Not sampling noise. Across 30 renders at 0.5/0.4/0.3/0.2 with `repetition_penalty`
  1.2/1.35 on unrelated texts, zero leaks — which is why the sweep looked clean while production was
  broken: the failure is text-specific, not rate-specific.
- **Shortening the prefix window.** `t3.hp.speech_cond_prompt_len` is the obvious knob and it is a
  trap. A truncated prefix gives the model a *fragment* of a sentence to continue, which is worse
  than a whole one. Measured on the turbo checkpoint, 6 texts each:

  | `speech_cond_prompt_len` | faithful | garbled | mean output length |
  |---|---|---|---|
  | 375 (library default) | 6/6 | 0 | 4.1 s |
  | 94 | 3/6 | 3 | 7.1 s |
  | 47 | 0/6 | 6 | **31.5 s** |
  | 24 | 0/6 | 6 | 28.5 s |
  | 8 | 0/6 | 6 | 19.5 s |

  `plen = 0` is not available either: `prepare_conditionals()` skips the conditioning block
  entirely and then fails on an unbound local. So the window stays full and only its **contents**
  change.

## What is the lever: put silence where the words were

Prepending more silence than the prefix window (7.5 s) means the LM's prefix contains no words at
all, while the voice encoder still sees the whole clip.

12 varied lines, production pod on `august-desktop` (RTX 2080 SUPER) running the **unpatched**
`0.1.7-cuda` image, with each reference passed per request so the padding is the only variable:

| reference | faithful | reference-content leaks | garbled | mean audio |
|---|---|---|---|---|
| raw `aussie-conv-ref.wav` | 7/12 | 3 | 2 | 3.5 s |
| raw + 3 s silence | 12/12 | 0 | 0 | 3.2 s |
| raw + 8 s silence | 11/12 | 1 | 0 | 3.4 s |

Padding 3 s and 5 s both still leaked once while 8 s did not, on a CPU probe run over 8 of these
lines (3 s and 5 s left words inside the 7.5 s prefix; 8 s did not). That is the mechanism confirming
itself.

## The second-order choice: do not widen the voice-decoder window

Padding moves speech out of `DEC_COND_LEN` too: with 8 s of silence and the library's 10 s window,
the *decoder* sees 8 s of silence plus the first 2 s of the clip. Tempting to widen it. Measured,
12 lines, CPU probe, voice-encoder cosine computed **only over faithful renders**:

| pad | decoder window | faithful | leaks | garbled | VE cosine | mean audio |
|---|---|---|---|---|---|---|
| 8 s | default (10 s) | **12/12** | 0 | 0 | 0.748 | 3.2 s |
| 8 s | widened to padded length | 11/12 | 0 | 1 | 0.789 | 3.6 s |
| 3 s | default (10 s) | 11/12 | 1 | 0 | 0.760 | 3.7 s |

Widening buys speaker similarity and costs both fidelity and VRAM: on the GPU, the widened build
held **3750 MiB** against the **3328 MiB** this service held before the change, on a card three
camry `object_detector` contexts (240 MiB each) are already using. So the default is **not to widen**
(`CHATTERBOX_REF_WIDEN_DECODER_WINDOW` is the dial if the timbre difference is audible enough to
argue about). Widening is bounded to 30 s regardless, because references enrolled through the browser
are deliberately uncapped and an unbounded window on a time-sliced shared GPU is how the pod dies.

Honest caveat: the one GPU run of the widened variant scored 12/12 with 0 leaks on these same 12
lines, where the CPU head-to-head scored it 11/12. Twelve lines does not separate the two on
faithfulness; it does show the un-widened variant does not leak, and it is strictly cheaper in VRAM,
which is why that is what ships.

An earlier version of this document quoted cosine 0.890 raw / 0.859 at 3 s / 0.845 at 8 s. Those
numbers were computed against the raw reference clip over *all* renders, including the ones that
were leaking its content — so part of what they measured was leakage, not voice. The 0.748/0.789
figures above are faithful-only.

## Where the pad lives in the code

`src/chatterbox_server.py`:

- `DEFAULT_REF_LEAD_SILENCE = 8.0`, `--ref-lead-silence` / `CHATTERBOX_REF_LEAD_SILENCE`.
- `State.conditioning_source()` builds a padded copy in `DEFAULT_REF_SCRATCH` (`/tmp`, container
  scratch — not the PVC, not in `TTS_ALLOW_ROOTS`), keyed on `(path, mtime_ns, size, pad)` so the
  encode happens once per clip and never on the request path.
- It is applied **in the service** rather than by editing the clip on the PVC, because voices
  enrolled through the browser arrive as raw recordings and need the same treatment. Pointing
  `CHATTERBOX_DEFAULT_REFERENCE` at a hand-padded file was the first attempt and is the wrong
  lever; the manifest names the raw clips again.
- `/health` reports `ref_lead_silence` and `ref_decoder_window_widened`.
- `CHATTERBOX_REF_LEAD_SILENCE=0` restores pre-fix behaviour.

Covered by `tests/test_reference_conditioning.py` (pad length/format, pad is actually silent and
the clip survives behind it, cache reuse, `0` disables, scratch outside the allow-roots, the
decoder window stays bounded, and the default pad is at least the prefix window). Run inside the
image so torch is present:

```sh
docker run --rm -v $PWD/tests:/t:ro -v $PWD/src:/src:ro \
  initialed85/edvox-tts-chatterbox:0.1.7.2-cuda python3 -m unittest discover -s /t -v
```

## Rollback

`CHATTERBOX_REF_LEAD_SILENCE="0"` on the same image restores the old conditioning exactly. Image
rollback is `0.1.7-cuda` (padding, widened window) or `0.1.7` + `CHATTERBOX_DEVICE=cpu`.

## Open questions

- 8 s is a *measured* pad for a 10.3 s clip against a 7.5 s window on this checkpoint. A much longer
  enrolled voice does not change the window, so the pad remains correct — but nobody has listened to
  a long enrolled voice at 8 s yet.
- Accent is the standing worry with Turbo (see `docs/tts-phase0-results.md`); removing the clip's
  words from the prefix also removes some of its *prosody* from the prefix, which may help or hurt
  the Australian-ness. Needs Ed's ears, not another probe.
- The `LEAK` regex used in every measurement here looks for hometown words (Tamworth/Sydney/"originally
  from"). It cannot detect a leak of content the clip does not contain; it is a leak detector for
  *this* clip, not a general one.
