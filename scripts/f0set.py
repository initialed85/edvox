"""Median fundamental frequency per clip: a coarse timbre check, not an accent test.

If a clone's median F0 sits far from the reference's, the conversion lost the voice.
Accent, idiom and expressiveness cannot be measured this way and still need Ed's ears.
"""
import os, sys, numpy as np, torchaudio

REF_F0 = 228.6  # measured on aussie-conv-ref.wav
d = sys.argv[1]
files = sorted(f for f in os.listdir(d) if f.endswith(".wav"))
print(f"{'clip':46s} {'dur':>6s} {'medF0':>7s} {'p10':>6s} {'p90':>6s} {'spread':>7s}")
for fn in files:
    x, sr = torchaudio.load(os.path.join(d, fn))
    x = x.mean(0).numpy().astype(np.float64)
    if len(x) < sr // 10:
        print(f"{fn:46s} too short"); continue
    fl, hop = int(0.04 * sr), int(0.01 * sr)
    f0s = []
    for i in range(0, len(x) - fl, hop):
        s = x[i:i + fl]
        if np.sqrt((s ** 2).mean()) < 0.015:
            continue
        s = s - s.mean()
        a = np.correlate(s, s, "full")[fl - 1:]
        if a[0] <= 0:
            continue
        a = a / a[0]
        lo, hi = int(sr / 320), int(sr / 85)
        seg = a[lo:hi]
        if len(seg) == 0 or seg.max() < 0.45:
            continue
        f0s.append(sr / (lo + int(seg.argmax())))
    if len(f0s) < 5:
        print(f"{fn:46s} {len(x)/sr:6.2f}s  unvoiced/tonal too low  n={len(f0s)}"); continue
    f0s = np.array(f0s)
    med, p10, p90 = np.median(f0s), np.percentile(f0s, 10), np.percentile(f0s, 90)
    print(f"{fn:46s} {len(x)/sr:6.2f}s {med:7.1f} {p10:6.1f} {p90:6.1f} {p90-p10:7.1f}")
