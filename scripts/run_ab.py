#!/usr/bin/env python3
"""Phase 0 A/B harness for the edvox TTS changeover.

Runs the fixed five-line corpus against each candidate model through audio.cpp's
CLI on the production node, and records wall time, audio duration, RTF and peak RSS.
Stdlib only. Deviations from docs/tts-changeover-spec.md section 4.4 are deliberate and
commented inline: a Python driver avoids shell-quoting damage to apostrophes and
em-dashes in the corpus, and /usr/bin/time is not present in the image, so peak RSS is
read from wait4(2) rusage instead.
"""
import csv
import os
import resource
import subprocess
import sys
import time
import wave

MODELS_ROOT = "/models/audio.cpp"
OUT_ROOT = "/audio/ab"
REF_PRIMARY = "/audio/voices/clone/aussie-conv-ref.wav"
REF_EXPRESSIVE = "/audio/voices/clone/aussie-expressive-ref.wav"

# CosyVoice3's zero_shot template requires the transcript of the prompt audio; without it
# the model emits ~0.5s of truncated garbage. Transcribed from the clips themselves with
# the production whisper, then corrected (whisper heard "Tanworth" and "bargains").
# Transcript must match the clip exactly, including the fact that the 10.3s window ends
# mid-sentence; a trimmed transcript made CosyVoice3 drop the first words of the target.
REF_TEXT = {
    REF_PRIMARY: ("So I live in Sydney in New South Wales but I am originally from a little "
                  "city called Tamworth which is in the northwest of New South Wales so it's "
                  "about six hours away from where"),
    "/audio/ab/ref16k.wav": ("So I live in Sydney in New South Wales but I am originally from "
                             "a little city called Tamworth which is in the northwest of New "
                             "South Wales so it's about six hours away from where"),
    "/audio/ab/refx16k.wav": ("The second thing is we are really, really lazy. Australians "
                              "have the laziest accents. I think of any nationality in the "
                              "world. Like I think that we sound like the biggest bogans ever."),
    REF_EXPRESSIVE: ("The second thing is we are really, really lazy. Australians have the "
                     "laziest accents. I think of any nationality in the world. Like I think "
                     "that we sound like the biggest bogans ever."),
}
CLI = "/app/audiocpp_cli"
THREADS = os.environ.get("AB_THREADS", "8")
RUN_TIMEOUT = int(os.environ.get("AB_TIMEOUT", "300"))

# (code, family, task, model_path, uses_reference, uses_language_flag, extra_args)
# uses_language_flag is per-family: cosyvoice3 rejects --language outright ("unknown
# CosyVoice3 request option: language"), which is what caused the first Phase 0 failure.
CANDIDATES = [
    ("chatterbox", "chatterbox", "clon", f"{MODELS_ROOT}/Chatterbox-GGUF", True, True, []),
    # CosyVoice3 needs a 16 kHz prompt clip: with the 24 kHz reference it emitted 0.4-1.2s
    # of truncated audio. Same speaker, same windows, resampled.
    ("cosyvoice3", "cosyvoice3", "clon", f"{MODELS_ROOT}/CosyVoice3-GGUF", True, False,
     ["--request-option", "template_name=zero_shot"], "/audio/ab/ref16k.wav",
     "/audio/ab/refx16k.wav"),
    ("index_tts2_5", "index_tts2", "clon", f"{MODELS_ROOT}/IndexTTS2.5-GGUF", True, True, []),
    # Turbo takes a single self-contained GGUF and cannot clone (supports_speaker_reference 0).
    ("chatterbox_turbo", "chatterbox_turbo", "tts",
     f"{MODELS_ROOT}/Chatterbox-Turbo-GGUF/chatterbox-turbo-q8_0.gguf", False, False, []),
]

FIELDS = ["model", "line", "ref", "audio_seconds", "wall_seconds", "rtf",
          "peak_rss_mb", "status", "notes"]


def wav_duration(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception as exc:  # noqa: BLE001
        print(f"    duration read failed: {exc}", file=sys.stderr)
        return None


def run_one(code, family, task, model, reference, text, out_path, extra, uses_lang=True,
            ref_text=None):
    cmd = [CLI, "--task", task, "--family", family, "--model", model,
           "--backend", "cpu", "--threads", THREADS, "--log",
           "--text", text, "--out", out_path]
    if reference:
        cmd += ["--voice-ref", reference]
        if ref_text:
            cmd += ["--reference-text", ref_text]
    if uses_lang:
        cmd += ["--language", "en"]
    cmd += extra
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    start = time.monotonic()
    status, notes = "ok", ""
    log_path = out_path + ".log"
    try:
        with open(log_path, "wb") as log:
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=RUN_TIMEOUT)
        if proc.returncode != 0:
            status = f"exit_{proc.returncode}"
    except subprocess.TimeoutExpired:
        status = f"timeout_{RUN_TIMEOUT}s"
    except Exception as exc:  # noqa: BLE001
        status = "error"
        notes = repr(exc)[:120]
    wall = time.monotonic() - start
    peak_mb = max(0, (resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss - before)) / 1024.0
    # ru_maxrss is high-water-mark of children; subtracting the previous HWM is not exact
    # when a child exceeds it, so report the absolute HWM too when the delta is zero.
    absolute_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0
    audio = wav_duration(out_path) if os.path.exists(out_path) else None
    caps = parse_caps(log_path)
    if audio:
        os.remove(log_path)
    return wall, audio, status, notes, absolute_mb, caps


def parse_caps(log_path):
    """Pull the runtime.* capability flags audio.cpp emits during load."""
    caps = {}
    try:
        with open(log_path, "r", errors="replace") as fh:
            for line in fh:
                if "supports_" in line or "language_count" in line:
                    parts = line.rstrip().split()
                    for i, tok in enumerate(parts):
                        base = tok.rsplit(".", 1)[-1]
                        if base.startswith("supports_") or base == "language_count":
                            if i + 1 < len(parts):
                                caps[base] = parts[i + 1]
    except OSError:
        pass
    return caps


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    corpus = []
    with open(f"{OUT_ROOT}/corpus.tsv") as fh:
        for row in fh:
            row = row.rstrip("\n")
            if row.strip():
                lid, text = row.split("\t", 1)
                corpus.append((lid, text))

    metrics_path = f"{OUT_ROOT}/metrics.csv"
    caps_path = f"{OUT_ROOT}/capabilities.csv"
    new_file = not os.path.exists(metrics_path)
    mfh = open(metrics_path, "a", newline="")
    writer = csv.DictWriter(mfh, fieldnames=FIELDS)
    if new_file:
        writer.writeheader()
        mfh.write("")

    for cand in CANDIDATES:
        code, family, task, model, uses_ref, uses_lang, extra = cand[:7]
        ref_conv = cand[7] if len(cand) > 7 else REF_PRIMARY
        ref_exp = cand[8] if len(cand) > 8 else REF_EXPRESSIVE
        if only and only != code:
            continue
        os.makedirs(f"{OUT_ROOT}/{code}", exist_ok=True)
        print(f"=== {code} ({family}/{task})", flush=True)
        caps_reported = False
        for lid, text in corpus:
            refs = [(ref_conv, "conv")]
            if uses_ref and lid == "01":
                refs.append((ref_exp, "expressive"))
            for reference, ref_label in refs:
                want_ref_text = code == "cosyvoice3"
                out = f"{OUT_ROOT}/{code}/{code}-{lid}-{ref_label}.wav"
                wall, audio, status, notes, peak_mb, caps = run_one(
                    code, family, task, model,
                    reference if uses_ref else None, text, out, extra, uses_lang,
                    (REF_TEXT.get(reference) if uses_ref and want_ref_text else None))
                rtf = round(wall / audio, 3) if audio else ""
                print(f"  {lid}/{ref_label}: audio={audio} wall={wall:.1f} "
                      f"rtf={rtf} peakRSS={peak_mb:.0f}MB {status} {notes}", flush=True)
                writer.writerow({"model": code, "line": lid, "ref": ref_label,
                                 "audio_seconds": round(audio, 3) if audio else "",
                                 "wall_seconds": round(wall, 2), "rtf": rtf,
                                 "peak_rss_mb": round(peak_mb),
                                 "status": status, "notes": notes})
                mfh.flush()
                if caps and not caps_reported:
                    with open(caps_path, "a") as cfh:
                        if os.stat(caps_path).st_size == 0:
                            cfh.write("model," + ",".join(sorted(caps)) + "\n")
                        cfh.write(code + "," + ",".join(caps[k] for k in sorted(caps)) + "\n")
                    print(f"  caps: {caps}", flush=True)
                    caps_reported = True
    mfh.close()


if __name__ == "__main__":
    main()
