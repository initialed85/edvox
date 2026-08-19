#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$ROOT/bin"
exec swiftc -O \
  -framework CoreGraphics \
  "$ROOT/src/keyboard_events.swift" \
  -o "$ROOT/bin/voice-keyboard-events"
