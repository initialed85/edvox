#!/usr/bin/env python3
"""Small client for the resident whisper-server inference endpoint."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} AUDIO_FILE", file=sys.stderr)
        return 2
    audio = Path(sys.argv[1]).expanduser()
    if not audio.is_file():
        print(f"audio file not found: {audio}", file=sys.stderr)
        return 2
    boundary = "----voicechatboundary"
    payload = audio.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    url = os.environ.get("STT_URL", "http://127.0.0.1:8178").rstrip("/") + "/inference"
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("STT_TIMEOUT", "120"))) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"STT server returned HTTP {error.code}: {error.read().decode(errors='replace')}") from error
    text = str(result.get("text", "")).strip()
    if not text:
        raise RuntimeError(f"STT server returned no text: {result!r}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
