#!/usr/bin/env python3
"""Backend policy tests (docs/tts-changeover-spec.md section 6.5).

These exist because the cue policy and the TTS URL were each encoded in three places at
once, so a new backend could ship with prose promising cues its model reads aloud as words.
Every test here asserts a *relationship* between those places rather than a value, which is
what makes adding a backend safe.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import web_server  # noqa: E402


class BackendCueMap(unittest.TestCase):
    def test_every_dispatchable_backend_is_in_the_map(self):
        # synthesize() raises for anything outside BACKEND_CUES, and the settings route
        # rejects the same set. If those two ever disagree, a session can select a backend
        # that health reports but synthesis refuses.
        self.assertEqual(set(web_server.BACKEND_CUES), set(web_server.CHATTERBOX_BACKENDS)
                         | set(web_server.CLI_BACKENDS)
                         | {"audiocpp", "qwen-service"})

    def test_map_values_are_boolean(self):
        for backend, cue in web_server.BACKEND_CUES.items():
            self.assertIsInstance(cue, bool, f"{backend} cue support must be a bool")

    def test_chatterbox_family_supports_cues_and_the_rest_do_not(self):
        # Measured in Phase 0: audio.cpp speaks "[chuckle]" as a word and Qwen has no cue
        # vocabulary. These assertions fail loudly if someone flips the policy without new
        # evidence, which is the point.
        for backend in web_server.CHATTERBOX_BACKENDS:
            self.assertTrue(web_server.cues_enabled(backend), backend)
        for backend in ("audiocpp", "qwen", "qwen-service"):
            self.assertFalse(web_server.cues_enabled(backend), backend)

    def test_unknown_backend_gets_the_conservative_answer(self):
        self.assertFalse(web_server.cues_enabled("not-a-backend"))


class PromptPolicy(unittest.TestCase):
    def test_placeholder_was_substituted(self):
        self.assertNotIn("__CUE_POLICY__", web_server.RUNTIME_SYSTEM_PROMPT)

    def test_cue_vocabulary_appears_in_the_prompt(self):
        for cue in web_server.CHATTERBOX_CUE_VOCAB:
            self.assertIn(f"[{cue}]", web_server.RUNTIME_SYSTEM_PROMPT, cue)

    def test_prompt_names_exactly_the_cue_capable_backends_in_order(self):
        sentence = web_server.cue_policy_sentence()
        voiced = sorted(b for b, ok in web_server.BACKEND_CUES.items() if ok)
        plain = sorted(b for b, ok in web_server.BACKEND_CUES.items() if not ok)
        self.assertIn(", ".join(voiced), sentence)
        self.assertIn(", ".join(plain), sentence)

    def test_prompt_is_generated_from_the_map_not_hardcoded(self):
        # The relationship that broke before: adding a backend must change the prose.
        original = dict(web_server.BACKEND_CUES)
        try:
            web_server.BACKEND_CUES["qwen-service"] = True
            self.assertIn("qwen-service", web_server.cue_policy_sentence().split("emit no")[0])
            web_server.BACKEND_CUES["chatterbox-turbo"] = False
            self.assertNotIn(
                "chatterbox-turbo",
                web_server.cue_policy_sentence().split("is one of")[1].split(":")[0],
            )
        finally:
            web_server.BACKEND_CUES.clear()
            web_server.BACKEND_CUES.update(original)


class UrlResolution(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("TTS_URL", "TTS_URL_CHATTERBOX", "TTS_URL_QWEN_SERVICE", "TTS_URL_AUDIOCPP")}
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_backends_resolve_to_distinct_urls(self):
        # The blocking flaw was one TTS_URL read by two branches, which made co-residency
        # impossible and a cutover more than one env change.
        os.environ["TTS_URL_CHATTERBOX"] = "http://chatterbox.svc:8190"
        os.environ["TTS_URL_QWEN_SERVICE"] = "http://qwen.svc:8191"
        self.assertEqual(web_server.tts_url_for("chatterbox"), "http://chatterbox.svc:8190")
        self.assertEqual(web_server.tts_url_for("chatterbox-turbo"), "http://chatterbox.svc:8190")
        self.assertEqual(web_server.tts_url_for("qwen-service"), "http://qwen.svc:8191")
        self.assertNotEqual(web_server.tts_url_for("chatterbox"),
                            web_server.tts_url_for("qwen-service"))

    def test_every_http_backend_resolves(self):
        for backend in web_server.BACKEND_CUES:
            if backend in web_server.CLI_BACKENDS:
                continue
            self.assertTrue(web_server.tts_url_for(backend).startswith("http://"), backend)

    def test_legacy_shared_url_still_works(self):
        os.environ["TTS_URL"] = "http://legacy:9999"
        self.assertEqual(web_server.tts_url_for("audiocpp"), "http://legacy:9999")

    def test_dedicated_variable_beats_legacy(self):
        os.environ["TTS_URL"] = "http://legacy:9999"
        os.environ["TTS_URL_QWEN_SERVICE"] = "http://qwen:8191"
        self.assertEqual(web_server.tts_url_for("qwen-service"), "http://qwen:8191")

    def test_cli_backend_has_no_url(self):
        with self.assertRaises(ValueError):
            web_server.tts_url_for("qwen")

    def test_settings_validation_and_policy_share_one_source(self):
        # The route's allow-list and the cue map were separate literals. normalize_backend is
        # now the only gate, so every mapped backend must pass and nothing else may.
        for backend in web_server.BACKEND_CUES:
            self.assertEqual(web_server.normalize_backend(backend), backend)
        for bad in ("chatterbox-tts", "", None, "index-tts", "qwen-service-turbo"):
            with self.assertRaises(ValueError):
                web_server.normalize_backend(bad)

    def test_backend_names_are_normalized_before_use(self):
        # A session could previously select "QWEN-SERVICE" and miss every dispatch branch.
        self.assertEqual(web_server.normalize_backend("  Chatterbox-Turbo "), "chatterbox-turbo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
