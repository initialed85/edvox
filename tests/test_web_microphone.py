#!/usr/bin/env python3
"""Browser regression test for the microphone and answer playback (web/app.js).

Guards the bug where a turn stopped the capture stream (vad-web's default pauseStream) and then
re-ran getUserMedia on resume (the default resumeStream, which the app also overrode with a fresh
request). On a phone that second request happens outside a user gesture, so iOS refuses it - the
answer arrived, the resume failed, and the session died demanding permission again.

The test drives the real app.js against a stub that models the two iOS rules and vad-web's
documented pause/resume contract, and asserts that a full turn performs no getUserMedia at all,
that the answer is actually played, and that an interrupted capture falls back to "tap the light"
rather than a red error.

Needs a Chrome-family browser; set VOX_BROWSER=/path/to/chrome if none is on PATH.
Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
BROWSER = ROOT / "tests" / "browser"
CANDIDATES = (
    os.environ.get("VOX_BROWSER"),
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def find_browser() -> str | None:
    for candidate in CANDIDATES:
        if not candidate:
            continue
        path = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if path:
            return path
    return None


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # noqa: D102 - keep unittest output readable
        return


class MicrophoneLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.browser = find_browser()
        if not self.browser:
            self.skipTest("no Chrome-family browser available (set VOX_BROWSER to run this test)")

    def _harness_dir(self) -> Path:
        work = Path(tempfile.mkdtemp(prefix="edvox-web-"))
        for item in WEB.iterdir():
            target = work / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
        shutil.copy2(BROWSER / "stub.js", work / "stub.js")
        shutil.copy2(BROWSER / "scenario.js", work / "scenario.js")
        # Built from the real index.html so the harness cannot drift away from production.
        page = (WEB / "index.html").read_text()
        self.assertIn('<script src="/app.js"></script>', page)
        page = page.replace(
            '  <script src="/app.js"></script>',
            '  <script src="stub.js"></script>\n  <script src="/app.js"></script>\n'
            '  <script src="scenario.js"></script>',
        )
        page = page.replace(
            '<div id="error" class="error" role="alert" hidden></div>',
            '<div id="error" class="error" role="alert" hidden></div>\n'
            '  <pre id="out" style="white-space:pre-wrap;font:11px monospace"></pre>',
        )
        page = re.sub(r"<title>.*?</title>", "<title>pending</title>", page, count=1)
        (work / "harness.html").write_text(page)
        return work

    def _run(self, work: Path) -> str:
        # SimpleHTTPRequestHandler resolves files against the process cwd, so the directory is
        # served from a thread while the cwd points at it.
        original_cwd = Path.cwd()
        os.chdir(work)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                completed = subprocess.run(
                    [
                        self.browser,
                        "--headless=new",
                        "--disable-gpu",
                        "--no-sandbox",
                        # Timers and awaits inside the page are advanced instantly; the budget is
                        # what lets a 20s playback fallback complete in a test.
                        "--virtual-time-budget=30000",
                        "--dump-dom",
                        f"http://127.0.0.1:{port}/harness.html",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        finally:
            os.chdir(original_cwd)
        if not completed.stdout.strip():
            self.fail(f"browser produced no DOM (rc={completed.returncode}): {completed.stderr[-800:]}")
        match = re.search(r'<pre id="out"[^>]*>(.*?)</pre>', completed.stdout, re.S)
        self.assertIsNotNone(match, "harness did not produce output; the page probably failed to load")
        return html.unescape(match.group(1))

    def test_turn_does_not_reask_for_the_microphone(self) -> None:
        work = self._harness_dir()
        try:
            output = self._run(work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        lines = [line for line in output.splitlines() if line.startswith(("PASS", "FAIL"))]
        failed = [line for line in lines if line.startswith("FAIL")]
        self.assertGreaterEqual(len(lines), 16, f"harness stopped early: {output}")
        self.assertEqual(failed, [], "\n".join(lines))
        self.assertNotIn("page errors", output, output)
        self.assertIn("prompts the user would have seen: 2", output, output)


if __name__ == "__main__":
    unittest.main()
