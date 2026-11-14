# Listening artefacts from the TTS changeover

**No audio in this tree is committed.** Every `.wav` under here is a clone of a real,
identifiable person and the repository is public; `*.wav` is gitignored at the repo root and
that rule is load-bearing. Never `git add -f` these. What *is* tracked is this README and the
docs that reference these paths.

## Listen to this first

`final/` — Chatterbox Turbo, `aussie-conv-ref.wav`, CPU, resident service, **temperature 0.5**:

| file | line | why it is here |
|---|---|---|
| `FINAL-A-idiom-temp05.wav` | "…servo pie… after the arvo." | the accent stress test |
| `FINAL-B-laugh-temp05.wav` | "Well [chuckle] that is actually pretty funny, innit?" | does the tag render as a laugh |
| `FINAL-C-story-temp05.wav` | long anecdote with idioms | holds the accent over a long turn |
| `FINAL-D-answer-temp05.wav` | short conversational reply | the shape a real turn takes |

The question these answer is whether shipping `temperature=0.5` costs us the expressiveness we
chose Turbo for. At the upstream default 0.8 the idiom line rendered correctly in only 2 of 6
draws, so 0.8 is not shippable; the trade is accuracy against flatness, and only ears decide it.

## Everything else (unreliable, kept for provenance)

- `service/` — renders produced through `src/chatterbox_server.py` over HTTP during Phase 2
  verification: `var-*.wav` (6 draws at temperature 0.8, the 2/6 result), `t0.5-*.wav` and
  `t0.3-*.wav` (4 draws each), `svc-check-*.wav` (the exaggeration-cache test), `final-*` (the
  pairs behind the tag-duration finding).
- `pod-dump/` — the whole `/audio/ab` working directory pulled off the bench pod, including
  the earlier blind set (`pod-dump/listen/`, `01-D04.wav` style names) and per-model render
  folders. Kept because it is the raw provenance of the Phase 0/1 tables.
- `accent/`, `tag/`, `mtl/`, `preflight/` — the Phase 0/1 A/B sets and the tag ablation.

**Caveat that applies to every Turbo file here:** any comparison whose only difference was
`exaggeration` is a comparison of two samples from the same distribution — the dial is inert on
the Turbo checkpoint (no `emotion_adv_fc` in `t3_turbo_v1.safetensors`). See
`docs/tts-phase0-results.md`, "Correction, again, and this time it is structural".
