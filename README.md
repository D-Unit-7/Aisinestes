<div align="center">

# Aisinestes

### Audio your AI can actually read.

**A calibrated quality gate for audio.** It doesn't hand you numbers and hope for the best —
it measures against real references and tells you what's wrong, in plain text,
with an exit code you can wire into CI.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![Tests](https://img.shields.io/badge/tests-34%20passing-brightgreen)
![Negatives](https://img.shields.io/badge/proven%20negatives-17-orange)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

</div>

---

## Why this exists

A language model cannot hear.

Ask it to help with your game's audio and it will answer — confidently — about something it never
perceived. It will agree that your impact sounds "punchy". It has no idea.

This tool was born from a real failure. Three impact effects were sent for review, and two of them
were broken: **98.5% of their energy sitting in the sub, no body, no bite.** The check in place at
the time was *"does the file contain audio?"* — and both passed it. The feedback that came back was
*"they still sound weak"*, and neither side could say why.

The problem was never the ears. It was that **nothing in the loop could look at the sound.**

Aisinestes is that missing piece. It turns a waveform into something a model — or a build pipeline —
can reason about and act on.

> *The name comes from **synesthesia**: perceiving one sense through another.
> Seeing sound, instead of hearing it.*

---

## What it does

Point it at a WAV and tell it what the audio is supposed to be:

```bash
python -m aisinestes impact_01.wav --genre fx-impact
```

It answers with a verdict, not a data dump:

```
-- Checks -----------------------------------------------------------------
  status  check                            measured           target
  ------  -------------------------------  -----------------  --------------------------------
  FLAG    Sub magnitude (20-60 Hz)         98.89 %            <= 60.00 %
  FLAG    Body magnitude (120-2000 Hz)     0.06 %             >= 5.00 %
  FLAG    Bite magnitude (2000 Hz and up)  0.13 %             >= 0.16 %
  OK      Fast attack (envelope peak)      0.000 of duration  <= 0.15 of duration (first 15 %)

  RESULT: 3 FLAG of 4 checks.
```

That's the broken impact from the story above. Here's a good one, same command:

```
  OK      Sub magnitude (20-60 Hz)         3.85 %             <= 60.00 %
  OK      Body magnitude (120-2000 Hz)     11.63 %            >= 5.00 %
  OK      Bite magnitude (2000 Hz and up)  84.43 %            >= 0.16 %
  OK      Fast attack (envelope peak)      0.000 of duration  <= 0.15 of duration (first 15 %)

  RESULT: 4 checks, none flagged.
```

And it works on music too:

```bash
python -m aisinestes track.wav --genre techno-club
```

```
  status  check                           measured     target
  ------  ------------------------------  -----------  ------------------------------------
  OK      Sub magnitude (20-60 Hz)        25.64 %      18.00 to 26.00 %  (reference ≈ 22 %)
  OK      Sub+bass magnitude (20-120 Hz)  48.98 %      48.00 to 52.00 %
  FLAG    Integrated loudness (LUFS-I)    -12.10 LUFS  -8.00 to -6.00 LUFS
  FLAG    Loudness range (LRA)            0.10 LU      5.00 to 8.00 LU
  OK      True peak                       -1.10 dBFS   <= -1.00 dBFS
```

Two flags, and both are actionable: the track is 4 dB quieter than club reference, and it's
completely flat dynamically. You know what to fix before you open a DAW.

---

## Use it as a gate

The exit code is the whole point:

| code | meaning |
|:---:|---|
| `0` | everything measured, no flags |
| `1` | at least one flag, or a metric couldn't be measured |
| `2` | couldn't produce a report at all |

So it drops straight into a pipeline:

```bash
# fail the build if any new sound effect is broken
for f in assets/sfx/*.wav; do
  python -m aisinestes "$f" --genre fx-impact || exit 1
done
```

```yaml
# .github/workflows/audio.yml
- name: Audio quality gate
  run: python -m aisinestes assets/sfx/impact.wav --genre fx-impact
```

No sound designer in the loop. No one guessing.

---

## What makes it different

Most tools in this space extract features and hand them over — spectral centroid, MFCCs, tempo,
loudness — leaving interpretation to whoever asked. For a human expert that's fine. **For a language
model it's a trap:** given a bare number with no reference, a model will confabulate a confident
opinion about it.

Aisinestes takes the opposite position.

**It judges instead of reporting.** Every number is checked against a calibrated reference for the
kind of audio you said it was. `25.64% sub` means nothing on its own; `25.64%, target 18–26%` is a
decision.

**Its thresholds come from data, not intuition.** The `fx-impact` profile was calibrated by measuring
**105 real impact sounds** from a public-domain library. The limits are the measured gaps between
sounds that work and sounds that don't — not numbers someone felt were about right. 100 of those 105
pass; the 5 that flag are genuinely dull thuds, and they flag for the right reason.

**It has no dependencies.** Pure Python standard library — the FFT, the WAV reader, the spectrogram
renderer, all of it. Nothing to install, nothing to break, nothing to audit. Optional loudness
metering uses `ffmpeg` if it happens to be on your PATH, and degrades gracefully if it isn't.

**It's built for sound effects, not just music.** Every comparable tool assumes you're analysing a
song. Game audio lives or dies on impacts, and impacts fail in ways a music profile can't see.

**Its own tests are proven to fail.** See below — this one matters more than it sounds.

---

## Profiles

| profile | for | checks |
|---|---|---|
| `fx-impact` | hits, impacts, one-shots | sub ceiling, body floor, bite floor, attack speed |
| `techno-club` | club-oriented electronic music | sub range, sub+bass range, LUFS-I, LRA, true peak |
| *(none)* | anything | measures everything, judges nothing |

Adding a profile is a dictionary in `targets.py` — thresholds, ranges, and the human-readable
sentence explaining what the profile expects.

---

## What you get

```
out/<filename>/
├── report.txt        the readable verdict shown above
├── report.json       every raw metric, for machines
├── spectrogram.png   log-frequency, 15 Hz–13 kHz, honest dB legend
└── waveform.png      full waveform
```

The spectrogram is deliberately built to be **looked at by a vision model** — logarithmic frequency
axis, labelled scale, no decorative gradients. When the numbers say "something is wrong in the low
mids", the image shows you where.

---

## How it measures

One decision is worth stating up front, because it changes every number:

**Bands are computed by magnitude `|X|`, not power `|X|²`.**

This was settled by experiment, not preference. The same audio measured both ways gives **42.25%
sub** by magnitude and **95.5%** by power. The magnitude figure reproduces careful manual
measurement; the power figure makes every genre reference unreachable. All references in this tool
are expressed in that convention — if you change how bands are computed, you must recalibrate the
targets, and the code says so in both places.

Analysis uses a Hann window of 8192 with 4096 hop, averaged across frames — up to ~200 frames evenly
sampled for long files, so a ten-minute track costs about the same as a ten-second one.

---

## Where the numbers come from

A tool that judges is only worth as much as the references it judges against. So here is every
threshold and how it got there — you should be able to audit this, disagree with it, or replace it.

### The genre references were researched, not invented

The `techno-club` targets — sub ≈ 22%, sub+bass 48–52%, −6 to −8 LUFS-I, LRA 5–8 LU, true peak
≤ −1 dBFS — come from published mastering references for the style, not from taste. They live in
`targets.py` with the reasoning next to them.

### The FX thresholds were measured against 105 real sounds

`fx-impact` was calibrated by running the tool over **105 impact sounds** from
[Kenney's public-domain library](https://kenney.nl/) — a large, freely licensed set of game audio
made by people who do this professionally.

The limits are the **measured gaps** between sounds that work and sounds that don't:

| check | threshold | how it was set |
|---|---|---|
| sub (20–60 Hz) | ≤ 60% | above this, impacts read as a rumble with no impact |
| body (120–2000 Hz) | ≥ 5% | below this, there is nothing to give the hit weight |
| bite (≥ 2000 Hz) | ≥ 0.16% | below this, the transient has no edge and reads as mushy |
| attack | peak in first 15% | a hit that peaks late isn't a hit |

**100 of the 105 pass.** The five that flag are all `impactSoft_heavy` — dull thuds with no mids and
no edge — and they flag for exactly the right reason. A calibration where everything passes hasn't
been calibrated; it's been fitted.

### The reference sample is deliberately a bad one

There's a known-flawed track kept as a measurement reference: its sub sits at roughly double the
target, it's about 15 dB quieter than club level, and its dynamic range is flat. **It must flag.**
Its job is not to be a gold standard — it's to prove the tool reproduces numbers that were measured
by hand independently. If it ever stops flagging, the calibration drifted.

---

## Rules the code follows

These aren't style preferences. Each one exists because of a specific failure mode, and they're
listed here so you can hold the code to them.

**No dependencies. No downloads. No network calls. Ever.**
The FFT, the WAV parser, the report writer — all standard library. This started as a hard constraint
on a machine where an extra dependency was an unacceptable risk, and it turned out to be the right
call for a different reason: **a tool an AI agent runs should not be able to install anything.** The
smaller the surface, the less there is to audit before you let something automated use it.

**No orphan processes.**
Every external call runs with a timeout and a guaranteed kill in `try/finally`. A tool that leaves
processes hanging is unusable in CI and merely annoying on a laptop — and both of those are the
tool's fault, not the user's.

**Never fake a number.**
If a metric can't be measured, the report says so and the exit code reflects it. Nothing is rounded
to a friendlier value, nothing degrades silently into a default. The rule in the code reads: *the
measured number is always visible.* A quality gate that hides its own uncertainty is worse than no
gate, because you'll trust it.

**No personal paths in the code.**
Machine-specific configuration goes in an ignored local file, never in a source comment. If you need
to point at an ffmpeg that isn't on your PATH, that's what `ffmpeg.local` is for.

**Cross-check against a reference implementation where one exists.**
The in-house loudness measurement is verified against `ffmpeg`'s EBU R128 implementation on every
test run. They agree to **0.010 dB**. Measuring something yourself and never comparing it to an
established implementation is how a tool becomes confidently wrong.

---

## The test suite

```bash
python harness/run_harness.py
```

```
SUMMARY: 34 cases in 2.6 s -> PASS=34
EXIT 0 (all PASS)
```

34 assertions in about three seconds. What matters is *how* they're built:

**Every family of checks has a proven negative.** For each test that must pass, there is a twin that
must *fail* — and the suite verifies that it actually fails. A detector that can't be made to fail
isn't detecting anything. Seventeen of the thirty-four cases exist purely to prove the other
seventeen aren't lying.

**The test signals are pure mathematics.** All ten WAV files are generated from code — sine waves,
filtered noise, exponential decays. Delete them, run `make_signals.py`, and you get back the same
files **bit for bit**. No samples, no licences, nothing borrowed.

Two of those signals deserve a mention:

```python
def gen_fx_roto(dur_s=0.5):
    """A broken impact: a 45 Hz thud and nothing else.
       No body, no bite. Reproduces the real failure this tool was built for."""

def gen_fx_bueno(dur_s=0.5):
    """A good impact: the three layers the profile asks for —
       thud at 45 Hz, body at 800 + 1300 Hz, bite from noise around 5 kHz."""
```

One must flag, the other must pass. That pair is the reason the detector can't quietly agree with
itself — and `fx_roto.wav` is the original mistake, preserved as a test so it can't happen twice.

There's also a cross-check against `ffmpeg`'s EBU R128 implementation: the in-house FFT and the
reference meter agree to **0.010 dB**.

---

## Install

```bash
git clone https://github.com/<user>/aisinestes
cd aisinestes
python -m aisinestes your_audio.wav --genre fx-impact
```

That's the whole installation. There is no `pip install` step because there is nothing to install.

If `ffmpeg` isn't on your PATH, point the tool at it:

```bash
export AISINESTES_FFMPEG=/path/to/ffmpeg      # Windows: set AISINESTES_FFMPEG=C:\...\ffmpeg.exe
```

**Optional:** if `ffmpeg` is on your PATH, you also get EBU R128 loudness (LUFS-I, LRA, true peak).
Without it, everything else still works.

---

## Limitations

Stated plainly, because a tool that judges should be judged back:

- **WAV only** (PCM 16/24/32-bit and IEEE float32). No MP3, no FLAC.
- **Two profiles so far.** Broad genre coverage is not the goal; calibrated coverage is.
- **BPM is solid on clean material and drifts about 1% on syncopated tracks.** Reported as an
  estimate, never as a check.
- **Report text is currently in Spanish.** The JSON is language-neutral.
- It measures spectral and dynamic properties. It cannot tell you whether a sound is *good* —
  only whether it matches the reference you asked it to match.

---

## Authorship

Written with AI assistance. The direction, planning, review and every design decision are the
repository owner's — including the calibration criteria, the hard rules the code follows, and each
threshold that made it into `targets.py`.

## Licence

MIT — see [LICENSE](LICENSE).

The ten test signals are generated by the included code and carry no third-party rights. The
`fx-impact` profile was calibrated by measuring sounds from [Kenney's](https://kenney.nl/)
public-domain library; no audio from it ships in this repository.
