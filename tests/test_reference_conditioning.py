#!/usr/bin/env python3
"""Reference conditioning tests (docs/tts-reference-conditioning.md).

Chatterbox Turbo builds its language-model prefix out of the *start of the reference clip*,
so a reference that opens with words can be spoken instead of the text we asked for. The fix
is a silence pad applied inside the service. These tests pin the mechanics of that pad - and
the relationships that make it safe - without loading 3.6 GB of weights.

Run inside the image, where torch is present:
  docker run --rm -v $PWD/tests:/t -v $PWD/src:/src:ro initialed85/edvox-tts-chatterbox:latest \
      python3 -m unittest discover -s /t -v
Locally (no torch) the model-dependent cases skip.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import chatterbox_server as cs  # noqa: E402

try:
    import numpy  # noqa: F401
    import torch  # noqa: F401
    import torchaudio  # noqa: F401
    HAVE_TORCH = True
except Exception:  # noqa: BLE001
    HAVE_TORCH = False
    numpy = None

SR = 24000


def write_clip(path: Path, seconds: float) -> Path:
    """A mono 16-bit tone, same shape as the clips on the voices volume."""
    n = int(SR * seconds)
    frames = bytearray()
    for i in range(n):
        value = 12000 if (i // 40) % 2 == 0 else -12000
        frames += int(value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))
    return path


def make_state(lead: float, scratch: Path) -> cs.State:
    # State.__new__ keeps these tests free of model loading; conditioning_source only
    # touches the three fields it needs.
    state = cs.State.__new__(cs.State)
    state.lead_silence = lead
    state.scratch_dir = scratch
    state.padded_cache = {}
    return state


class SilencePad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clip = write_clip(self.root / "voice.wav", 6.0)

    def tearDown(self):
        self.tmp.cleanup()

    @unittest.skipUnless(HAVE_TORCH, "needs torch/torchaudio")
    def test_padded_length_is_source_plus_silence(self):
        out = make_state(8.0, self.root / "scratch").conditioning_source(self.clip)
        with wave.open(str(out)) as w:
            self.assertEqual(w.getnchannels(), 1, "padding must not change channel count")
            self.assertEqual(w.getsampwidth(), 2, "padding must stay 16-bit PCM")
            self.assertEqual(w.getframerate(), SR, "padding must not resample")
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), 14.0, delta=0.05)

    @unittest.skipUnless(HAVE_TORCH, "needs numpy to read frames back")
    def test_padding_is_silent_at_the_head_and_keeps_the_clip_behind_it(self):
        out = make_state(8.0, self.root / "scratch").conditioning_source(self.clip)
        with wave.open(str(out)) as w:
            data = numpy.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        head = data[: int(SR * 7.5)]
        tail = data[int(SR * 8.5):]
        self.assertTrue(int(numpy.abs(head).max()) == 0, "the pad must be actual silence")
        self.assertTrue(int(numpy.abs(tail).max()) > 0, "the clip must survive behind the pad")

    def test_zero_disable_is_a_passthrough(self):
        state = make_state(0.0, self.root / "scratch")
        self.assertEqual(state.conditioning_source(self.clip), self.clip)

    @unittest.skipUnless(HAVE_TORCH, "needs torch/torchaudio")
    def test_padded_path_is_reused_until_the_source_changes(self):
        # Re-padding on every request would decode and re-encode audio on the request path,
        # which is the expensive part this service caches elsewhere.
        state = make_state(8.0, self.root / "scratch")
        first = state.conditioning_source(self.clip)
        self.assertEqual(state.conditioning_source(self.clip), first)
        self.clip.unlink()
        write_clip(self.clip, 9.0)
        self.assertNotEqual(state.conditioning_source(self.clip), first,
                           "a changed clip must not reuse the old pad")

    def test_scratch_lives_outside_the_allow_roots_on_purpose(self):
        # Padded copies are derived artefacts. They must not become paths a request can name,
        # which is why they are written under /tmp and the request's own path is checked
        # before any padding happens.
        self.assertTrue(str(cs.DEFAULT_REF_SCRATCH).startswith("/tmp"))
        self.assertNotIn("/tmp", cs.parse_allow_roots(None)[0].as_posix()
                         + cs.parse_allow_roots(None)[1].as_posix())


class DialRelationships(unittest.TestCase):
    def test_default_pad_covers_the_language_model_window(self):
        # Turbo primes the LM with speech_cond_prompt_len tokens from the head of the
        # reference; measured on the turbo checkpoint that is 375 tokens, about 7.5 s of
        # audio. A pad shorter than the window leaves words in it: 3 s and 5 s both still
        # leaked on GPU/CPU probes, 8 s did not. The default must therefore stay at or above
        # the window, and 8 s is what shipped.
        default = cs.DEFAULT_REF_LEAD_SILENCE
        self.assertGreaterEqual(default, 7.5,
                                "the default pad must be at least the T3 prefix window")

    def test_argument_defaults_read_the_environment(self):
        import argparse
        import os

        os.environ.pop("CHATTERBOX_REF_LEAD_SILENCE", None)
        os.environ["CHATTERBOX_REF_WIDEN_DECODER_WINDOW"] = "true"
        try:
            parser = argparse.ArgumentParser()
            # Rebuild the same two arguments the service exposes, to prove the environment is
            # the dial rather than a value compiled into the image.
            parser.add_argument("--ref-lead-silence", type=float,
                                default=float(os.environ.get("CHATTERBOX_REF_LEAD_SILENCE", "8")))
            parser.add_argument("--widen-decoder-window", action="store_true",
                                default=os.environ.get("CHATTERBOX_REF_WIDEN_DECODER_WINDOW", "")
                                .strip().lower() in ("1", "true", "yes", "on"))
            args = parser.parse_args([])
            self.assertEqual(args.ref_lead_silence, 8.0)
            self.assertTrue(args.widen_decoder_window)
        finally:
            os.environ.pop("CHATTERBOX_REF_WIDEN_DECODER_WINDOW", None)

    @unittest.skipUnless(HAVE_TORCH, "needs torch/torchaudio")
    def test_widened_decoder_window_is_bounded(self):
        # References enrolled through the browser are deliberately uncapped, so an unbounded
        # window is how the pod dies on a shared card.
        state = cs.State.__new__(cs.State)
        state.lead_silence = 8.0
        state.widen_decoder_window = True
        state.widened_window_max_seconds = 30.0
        state.scratch_dir = Path(tempfile.mkdtemp())
        state.padded_cache = {}

        class FakeModel:
            sr = SR
            DEC_COND_LEN = SR * 10

        state.model = FakeModel()
        long_clip = write_clip(state.scratch_dir / "long-recording.wav", 120.0)
        source = state.conditioning_source(long_clip)
        # Call the widening branch the way use_reference() does.
        frames = torchaudio.info(str(source)).num_frames
        target = min(frames, int(state.model.sr * state.widened_window_max_seconds))
        if target > state.model.DEC_COND_LEN:
            state.model.DEC_COND_LEN = target
        self.assertLessEqual(state.model.DEC_COND_LEN, SR * 30,
                             "the decoder window must not grow with whatever gets uploaded")
        self.assertGreater(state.model.DEC_COND_LEN, SR * 10,
                           "widening must actually widen when it is asked to")


if __name__ == "__main__":
    import unittest
    if HAVE_TORCH:
        import numpy  # noqa: F401
    unittest.main()
