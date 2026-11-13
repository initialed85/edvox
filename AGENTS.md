# AGENTS.md

## Scope

This repository is the local voice-chat project on `icarus.local`, normally at
`/Users/edwardbeech/Projects/Home/voice-chat`. It is a deliberately modular
pipeline:

```text
browser/phone → Caddy HTTPS :8443 → Python web server :8200
             → Whisper :8178 → llama-server :8088 → TTS :8190
```

The browser frontend, web orchestrator, Whisper, chat model, and TTS backend
should remain independently replaceable.

## Working style

- Inspect the repository, dirty state, process ownership, listeners, and health
  endpoints before changing anything.
- Preserve unrelated user changes. Do not use `git reset`, `git stash`, broad
  overwrites, or cleanup commands to make the tree convenient for the agent.
- Do not commit or push unless the user explicitly asks. Report separately what
  was inspected, modified, applied, committed, pushed, and verified.
- Keep the user informed in concise terms, especially before disruptive actions.
- Match the existing code and documentation style; prefer small, direct changes.

## Resident-service ownership

Development may require stopping or killing resident instances of the web
server, Caddy, Whisper, llama-server, or TTS. That is allowed, but call it out
explicitly before doing it and again in the final report. First identify the
exact process, owner, command line, listener, and health state; do not use broad
pattern kills when a specific PID can be targeted.

Record which instances existed before testing. Any resident instance started by
the agent must be stopped before finishing. Do not leave agent-started
background daemons, replacement model servers, or test services running. This
leaves the ports and ownership available for the user to start their preferred
foreground daemons manually. If a pre-existing user-owned daemon is stopped,
make its final state explicit and do not silently substitute an agent-managed
replacement.

After a restart or deployment, verify the actual process, listener, health
endpoint, route, and representative behavior. A successful start command alone
is not verification. Be particularly careful to distinguish model-weight
residency from cached reference conditioning in Chatterbox.

## Voice-chat conventions

- `/` is the locked minimal conversation UI with VOX enabled on open; `/admin/`
  is the full operational/settings UI and is not an authentication boundary.
- Keep the immutable runtime/voice contract separate from the editable persona.
- Root VOX uses self-hosted Silero VAD v5. Preserve the currently validated
  800 ms pre-padding, 350 ms minimum speech, 2 s redemption period, and genuine
  half-duplex microphone release unless testing demonstrates a reason to change
  them.
- Keep VAD, endpointing, noise suppression, microphone lifecycle, and playback
  concerns separate. Release microphone/audio resources before reply playback.
- Keep conversation state, timing metadata, archived input/output audio, clone
  references, model weights, and cached voice conditionals as distinct things.
- Current preferred resident TTS is Chatterbox Turbo on MPS; the backend remains
  replaceable.
- The archive is intentionally local and has no automatic retention policy:
  session manifests, timestamped media, and per-session LLM JSONL logs are
  retained for manual pruning.

## Verification

Use syntax checks and focused endpoint tests after edits. For frontend changes,
refresh the browser; ordinary static assets do not use hot reload. For Python,
Caddy, or inference-service changes, restart or reload only the affected
component and verify it. Never describe a change as live without checking the
correct remote process and behavior.

When working through the public route, verify both the local upstream and
`https://edvox.dev.initialed85.cc`; the public path is Traefik to Mac Caddy and
uses the configured upstream TLS/SNI handling.

## Remote tooling

The remote Mac may not have Node/npm. Vendor browser assets explicitly and add
static mappings when needed. Use absolute paths on macOS, verify the remote
working directory before transfers, and remember that macOS `base64` and
`find` options differ from GNU utilities. Prefer targeted commands over assumed
Linux flags.
