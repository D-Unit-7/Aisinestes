# Measurement methodology

Every number this tool prints depends on the decisions below. They are stated here so you
can audit them, disagree with them, or replace them.

## Bands are computed by magnitude, not power

**Bands are computed by magnitude `|X|`, not power `|X|²`.**

This was settled by experiment, not preference. The same audio measured both ways gives:

| convention | sub share on the reference track |
|---|---|
| magnitude `\|X\|` | 42.25 % |
| power `\|X\|²` | 95.5 % |

The magnitude figure reproduces the careful manual measurement of that file (42.9 %); the
power figure makes every genre reference unreachable.

All references in this tool are expressed in the magnitude convention. **If you change how
bands are computed, you must recalibrate every target.** The code says so in both places —
in the analysis module and next to the reference values.

Why the discrepancy is so large: squaring the spectrum exaggerates the dominance of the
loudest partials. In music with a strong low end, the sub-bass bin dominates the sum of
squares almost completely, which is why the power convention reports 95.5 % sub for a
track that is plainly not 95.5 % sub-bass by ear or by careful hand measurement.

## FFT and framing

- **Window:** Hann, 8192 samples.
- **Hop:** 4096 samples (50 % overlap).
- **Frame sampling:** frames are averaged across the file. For long files, up to ~200
  evenly spread frames are sampled rather than every frame — so a ten-minute track costs
  about the same as a ten-second one, and the spectral profile stays representative.
- **Implementation:** an in-house iterative radix-2 FFT written against the Python
  standard library (`array`, `cmath`, `math`). No NumPy, no SciPy.

Mono is analysed directly; multi-channel input is mixed down before analysis.

## Bands

| band | range |
|---|---|
| sub | 20–60 Hz |
| bass | 60–120 Hz |
| low_mids | 120–350 Hz |
| mids | 350–2000 Hz |
| high_mids | 2000–8000 Hz |
| air | 8000–16000 Hz |

Derived groupings used by the `fx-impact` profile:

- **body** = low_mids + mids (120–2000 Hz)
- **bite** = high_mids + air (2000 Hz and up)

Every report prints a band sum as a sanity check; it must come out at ~100 %.

## Loudness

LUFS-I, LRA and true peak are **not** computed in-house. They come from `ffmpeg`'s
`ebur128` filter, which implements EBU R128. When `ffmpeg` is unavailable these metrics
are reported as unmeasured — never estimated, never defaulted.

The test suite includes a cross-check on a synthetic 1 kHz sine: our own RMS-in-dBFS and
ffmpeg's LUFS-I agree within **0.010 dB** on that signal. This is a narrow check by
design — on a 1 kHz tone the K-weighting used by R128 is essentially flat, so the two
figures are expected to coincide, and any drift indicates a problem in the sample
pipeline (scaling, bit depth, channel handling). It is **not** a claim that the in-house
analysis implements EBU R128.

## Images

`spectrogram.png` and `waveform.png` are rendered by `ffmpeg` (`showspectrumpic` and
`showwavespic`), not by in-house code. Options are chosen for readability rather than
looks:

- `fscale=log` — logarithmic frequency axis, so the low end where impact problems are
  decided gets real estate.
- `legend=1` — axes and a dB bar. Without the legend the image is only good for looking
  at blobs, not for reading values.
- `gain=1` — no boost. Raising the gain brightens the image but misaligns the colours
  against the legend's dB scale, which would make the legend lie.

## Onsets and BPM

Onsets are detected from the energy envelope. BPM is derived from onset intervals and is
reported as an **estimate**: it is solid on clean material and drifts around 1 % on
syncopated tracks. It is deliberately never used as a pass/fail check.
