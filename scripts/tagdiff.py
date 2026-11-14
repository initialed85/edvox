"""Locate and characterise the extra acoustic material a paralinguistic tag inserts.

Compares the tagged render against the untagged twin by aligning on the trailing speech
(from the end, since the tag sits near the start) and reporting the energy and spectral
flatness of the region unique to the tagged version. Laughter is noisy-voiced: moderate
flatness with strong energy. A spoken word is tonal: low flatness.
"""
import numpy as np, sys
import torchaudio

def load(p):
    x, sr = torchaudio.load(p)
    x = x.mean(0).numpy().astype(float)
    # normalise so the float and PCM files are on the same scale
    return x / max(1e-9, np.abs(x).max()) * 0.9 if np.abs(x).max() > 1.5 else x, sr

def env(x, sr, ms=20):
    fl = max(1, int(sr*ms/1000)); m = len(x)//fl
    return np.sqrt((x[:m*fl].reshape(m, fl)**2).mean(1)), fl, sr

def flat(seg):
    if len(seg) < 64: return float('nan')
    P = np.abs(np.fft.rfft(seg*np.hanning(len(seg))))**2 + 1e-12
    return float(np.exp(np.mean(np.log(P)))/np.mean(P))

def tonal(x, sr):
    # voiced fraction via autocorrelation
    fl, m = int(0.03*sr), 0
    v = 0; tot = 0
    for i in range(0, len(x)-fl, fl//2):
        s = x[i:i+fl]
        if np.sqrt((s**2).mean()) < 0.01: continue
        tot += 1
        a = np.correlate(s, s, 'full')[fl-1:]
        a = a/a[0] if a[0] > 0 else a
        z = a[int(0.0077*sr):int(0.0125*sr)]
        if len(z) and z.max() > 0.5: v += 1
    return v/tot if tot else 0.0

tagged, sr = load(sys.argv[1]); base, _ = load(sys.argv[2])
et, _, _ = env(tagged, sr); eb, _, _ = env(base, sr)
extra = len(et) - len(eb)
print(f"tagged  {len(tagged)/sr:.2f}s  frames={len(et)}  voiced={tonal(tagged,sr):.2f}")
print(f"base    {len(base)/sr:.2f}s  frames={len(eb)}  voiced={tonal(base,sr):.2f}")
if extra > 0:
    # the tag sits early: look at the first region where tagged has energy and base does not
    thr = 0.05*et.max()
    on_t = et > thr
    head = int(np.argmax(on_t))
    seg = tagged[head*int(0.02*sr): (head+max(0,extra))*int(0.02*sr) + int(0.02*sr)]
    print(f"first active frame at {head*20}ms, extra frames={extra} (~{extra*20}ms)")
    print(f"unique-region peak={np.abs(seg).max():.3f} rms={np.sqrt((seg**2).mean()):.3f} "
          f"flatness={flat(seg):.4f} voiced={tonal(seg,sr):.2f}")
    print("  (laughter/noise-like -> flatness > 0.005 and voiced < 0.7; a spoken word is "
          "flatness < 0.001 and voiced > 0.8)")
