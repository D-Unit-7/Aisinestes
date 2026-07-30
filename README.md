<div align="center">

# Aisinestes

### Audio your AI can actually read.

**A calibrated quality gate for audio.** It turns a WAV into visual snapshots and
pass/fail checks measured against real references — in plain text, with an exit code
you can wire into CI.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Core](https://img.shields.io/badge/core-stdlib%20only-brightgreen)
![FFmpeg](https://img.shields.io/badge/ffmpeg-optional-yellow)
![Tests](https://img.shields.io/badge/tests-78%20passing-brightgreen)
![Negatives](https://img.shields.io/badge/proven%20negatives-39-orange)
![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-lightgrey)

</div>

---

## Why this exists

Coding assistants can help you generate and integrate game audio, but they cannot directly
inspect the WAV file inside the same workflow. They will answer — confidently — about a
sound they never examined.

This project started with a track. The assistant composed music for the game and then
could not evaluate a single second of it. Measured later, it sat roughly 15 dB below club
reference with about twice the sub-bass it should have had — none of which was visible
from inside the workflow that produced it.

Then the same blind spot shipped. Two impact effects passed the only check in place at the
time — *"does the file contain audio?"* — and both were broken: almost all of their energy
in the sub-bass, no body, no high-frequency attack. The feedback that came back was *"they
still sound weak"*, and nothing in the loop could say why.

The AI could suggest changes. It had no reliable representation of what was actually inside
the file. **Aisinestes was built to provide that missing representation** — and the first
thing it did was fix that track. The numbers are further down, including the one that got
*worse*.

*(Full story: [docs/ai-development-process.md](docs/ai-development-process.md).)*

```
   WAV file
      ↓
  Aisinestes
      ↓
  waveform + spectrogram + calibrated checks
      ↓
  AI-assisted diagnosis  or  CI decision
```

> *The name comes from **synesthesia**: perceiving one sense through another.
> Seeing sound, instead of hearing it.*

---

## What it looks like

The same command on a broken impact and a healthy one:

| Broken impact | Healthy impact |
|---|---|
| ![broken](docs/images/impact-broken-spectrogram.png) | ![healthy](docs/images/impact-healthy-spectrogram.png) |
| **3 of 4 checks flagged** | **no checks flagged** |
| everything crammed below 90 Hz, silence above | sub, body at 800/1300 Hz, and bite up top |

The spectrogram is deliberately built to be **looked at** — logarithmic frequency axis,
labelled dB scale, no decorative gradients. When the numbers say "something is wrong in
the low mids", the image shows you where.

---

## Quick example

```bash
python -m aisinestes impact_01.wav --genre fx-impact
```

```
  status  check                            measured           target
  ------  -------------------------------  -----------------  --------------------------------
  FLAG    Sub magnitude (20-60 Hz)         98.89 %            <= 60.00 %
  FLAG    Body magnitude (120-2000 Hz)     0.06 %             >= 5.00 %
  FLAG    Bite magnitude (2000 Hz and up)  0.13 %             >= 0.16 %
  OK      Fast attack (envelope peak)      0.000 of duration  <= 0.15 of duration (first 15 %)

  RESULT: 3 FLAG of 4 checks.
```

It works on music too:

```bash
python -m aisinestes track.wav --genre techno-club
```

```
  OK      Sub magnitude (20-60 Hz)        25.64 %      18.00 to 26.00 %  (reference ≈ 22 %)
  OK      Sub+bass magnitude (20-120 Hz)  48.98 %      48.00 to 52.00 %
  FLAG    Integrated loudness (LUFS-I)    -12.10 LUFS  -8.00 to -6.00 LUFS
  FLAG    Loudness range (LRA)            0.10 LU      5.00 to 8.00 LU
  OK      True peak                       -1.10 dBFS   <= -1.00 dBFS
```

Both flags are actionable: the track is 4 dB quieter than club reference, and it is
dynamically flat. You know what to fix before opening a DAW.

### A real before and after

The same track, measured before and after a round of corrections driven by these numbers:

| metric | before | after | reference |
|---|---:|---:|---|
| sub (20–60 Hz) | 42.25 % 🚩 | **25.64 %** ✅ | 18–26 % |
| sub+bass (20–120 Hz) | 52.21 % 🚩 | **48.98 %** ✅ | 48–52 % |
| integrated loudness | −22.70 LUFS 🚩 | **−12.10 LUFS** 🚩 | −8 to −6 |
| loudness range | 0.80 LU 🚩 | **0.10 LU** 🚩 | 5–8 |
| | *4 of 5 flagged* | *2 of 5 flagged* | |

The spectral balance was the target and it moved into range. Loudness improved by 10.6 LU
and still flags.

*The reference column is the `techno-club` profile's own window, not a published standard —
see [docs/references.md](docs/references.md) for what backs each of its five values.*

**And one metric got worse.** Pushing the level flattened the dynamics further — the
loudness range fell from 0.80 LU to 0.10. Nobody noticed at the time; the tool did, on a
later measurement. That is the argument for the whole project in one line: an unmeasured
fix is a guess about which trade you just made.

---

## What you get

```
out/<filename>/
├── report.txt        the readable verdict shown above
├── report.json       every raw metric, for machines
├── report.html       the same report as one self-contained page, safe to share
├── spectrogram.png   log-frequency, ~15 Hz-13 kHz, honest dB legend   (needs ffmpeg)
└── waveform.png      full waveform                                     (needs ffmpeg)
```

The parent folder is `out` by default and moves with `--out DIR`.

Without `ffmpeg` you still get both reports and every spectral check; you lose the two
images and the EBU R128 loudness metrics.

---

## Use it as a gate

| exit code | meaning |
|:---:|---|
| `0` | everything measured, no flags |
| `1` | at least one flag — the audio was judged and it failed |
| `2` | couldn't produce a report at all (missing file, unsupported WAV) |
| `3` | no flags, but at least one metric was never measured — for example `fx-impact` with no `ffmpeg` (its four checks pass, loudness stays unknown) |

A flag outranks a missing metric: a run with both returns `1`. Code `3` exists so a gate
can tell *clean* apart from *clean as far as it got*, and decide on purpose which of the
two it accepts. `report.json` carries the same thing in its `verdict` block
(`flags`, `checks`, `unmeasured`, `exit_code`).

One consequence worth knowing: when a profile **checks** a metric that could not be
measured — `techno-club` checks loudness, so no `ffmpeg` means three of its checks have no
data — those checks fail closed as `FLAG` and the run exits `1`, not `3`. A gate never
goes green, or even "incomplete", on something it was explicitly asked to judge and
couldn't.

```bash
# fail the build if any new sound effect is broken
for f in assets/sfx/*.wav; do
  python -m aisinestes "$f" --genre fx-impact || exit 1
done
```

That loop fails on `3` as well, which is usually what you want on CI: a check that never
ran is not a check that passed. To accept incomplete runs, test for exit code `1` instead.

Measurable technical failures get caught before subjective review, and every flag says
what to inspect.

---

## The short output

The full report is written for a person looking at a screen. `--brief` is written for
whatever reads output by the line — a coding agent, a CI log, a script:

```bash
python -m aisinestes techno.wav --genre techno-club --brief
```

```
AISINESTES techno.wav | genre=techno-club v1 | 7.273s 48000Hz
VERDICT: FLAG (4 of 5)
FLAG Sub magnitude (20-60 Hz): 42.25 % vs 18.00 to 26.00 % (reference ≈ 22 %)
FLAG Sub+bass magnitude (20-120 Hz): 52.21 % vs 48.00 to 52.00 %
FLAG Integrated loudness (LUFS-I): -22.70 LUFS vs -8.00 to -6.00 LUFS
FLAG Loudness range (LRA): 0.80 LU vs 5.00 to 8.00 LU
files: out/techno/
exit=1
```

It writes exactly the same files as a normal run; what changes is what it prints. The
checks that passed are left out on purpose — a summary that lists everything costs the
same to read as the report it summarises. What stays is what changes a decision.

The shape is fixed and bounded: at most twenty lines, one item per line, four of them
always there (header, verdict, `files:`, `exit=`). When more failures turn up than fit,
the rest are **counted** in a last line that names the file holding all of them — a
truncated list that looks complete is worse than a long one. No line ever carries an
absolute path: the audio is named by basename and even the reasons `ffmpeg` gives are
scrubbed, because this is the output most likely to be pasted somewhere else.

The verdict is one of four words:

| word | means |
|---|---|
| `CLEAN` | every check passed, everything measured |
| `FLAG` | at least one check failed |
| `INCOMPLETE` | nothing failed, but something was never measured |
| `NOT JUDGED` | the profile has no checks — nothing was tested |

The fourth exists because the alternative was a lie. With `--genre none` there are zero
checks and therefore zero failures, and calling that `CLEAN` would claim a pass nobody
ever tested for. For the same reason, **`--genre none` cannot be used as a gate**: it
measures, it never fails, and in CI it produces a green light that tested nothing.

`--brief` works with `--compare` too, and keeps the same shape — see
[docs/cli-and-outputs.md](docs/cli-and-outputs.md) for both formats in full.

---

## Compare two versions

```bash
python -m aisinestes before.wav after.wav --compare --genre fx-impact
```

```
  metric                           old      new      delta    direction  transition
  -------------------------------  -------  -------  -------  ---------  ----------
  Sub magnitude (20-60 Hz)         98.890   2.120    -96.770  improved   fixed
  Body magnitude (120-2000 Hz)     0.060    9.680    +9.620   improved   fixed
  Bite magnitude (2000 Hz and up)  0.130    88.020   +87.890  improved   fixed
  Fast attack (envelope peak)      0.000    0.510    +0.510   worsened   broke
```

Three metrics repaired and one broken in the same round of fixes: the same kind of trade
this README opens with, except caught while it happens instead of on a later measurement.
(The four rows above are the real output for those two files, minus the `target` column
and the loudness rows, which the terminal prints too.)

`direction` reads the *meaning* of each check, not the sign of the difference: for a
ceiling, going down is better; for a floor, going up is better; for a range, getting
closer to it is better. Crossing the threshold outranks everything else — if the check
flipped, the direction says so however small the step was. A metric the profile has no
reference for still shows its real delta, and says it cannot judge it. A side that was
never measured comes out `not comparable` — never as a zero.

**The comparison gates on the new file.** Its verdict is the exit code, with the same
four codes as a single run: the old file is context, not a vote. Outputs go to
`out/compare_<old>_vs_<new>/` as `compare.txt`, `compare.json` and `compare.html`, and
`--brief` prints one line per metric instead of the table.

### The HTML report

Every run also writes `report.html`, and `--compare` writes `compare.html`: the same
verdict, tables and images as one **self-contained** page — inline CSS, the PNGs embedded
as data URIs, zero external requests of any kind. It names the audio by basename and
never carries the path it was measured from, which is what makes it the one output safe
to hand to somebody else. No generation timestamp either: two runs over the same file
produce the same bytes, so a diff between two pages is about the audio.

---

## What makes it different

- **It returns calibrated checks, not unexplained raw metrics.** `25.64 % sub` means
  nothing on its own; `25.64 %, target 18–26 %` is a decision. Given a bare number with no
  reference, a language model will confabulate a confident opinion about it.
- **It supports short game sound effects, not only full music tracks.** Impacts fail in
  ways a music profile cannot see.
- **It can be used as a command-line quality gate**, with an exit code and no interactive step.
- **Its test suite includes explicit negative cases that must fail.** Thirty-nine of the
  seventy-eight cases exist purely to prove the other thirty-nine aren't lying.

---

## Profiles

| profile | for | checks |
|---|---|---|
| `fx-impact` | hits, impacts, one-shots | sub ceiling, body floor, bite floor, attack speed |
| `techno-club` | club-oriented electronic music | sub range, sub+bass range, LUFS-I, LRA, true peak |
| *(none)* | anything | measures everything, judges nothing |

Adding a profile is a dictionary in `targets.py` — see
[docs/adding-profiles.md](docs/adding-profiles.md).

---

## Install

```bash
git clone https://github.com/D-Unit-7/Aisinestes.git
cd Aisinestes
python -m aisinestes your_audio.wav --genre fx-impact
```

That is the whole installation — there is no `pip install` step because there are no
third-party Python packages to install.

**Requirements**

- Python 3.11+ *(developed and tested on 3.13)*
- Tested on Windows. Nothing in the code is platform-specific, but other platforms have
  not been verified.
- `ffmpeg` **optional** — enables the spectrogram, the waveform and EBU R128 loudness
  (LUFS-I, LRA, true peak). Everything else works without it.

If `ffmpeg` isn't on your PATH:

```bash
export AISINESTES_FFMPEG=/path/to/ffmpeg      # Windows: set AISINESTES_FFMPEG=C:\...\ffmpeg.exe
```

---

## Tests

```bash
python harness/run_harness.py
```

```
SUMMARY: 78 cases in 10.1 s -> PASS=78
EXIT 0 (all PASS)
```

Each positive detector test has a corresponding negative case that must fail — and the
suite verifies that it actually fails. A detector that cannot be made to fail isn't
detecting anything. The eleven WAV fixtures are generated from code (sine waves, filtered
noise, exponential decays) and can be recreated bit for bit; no samples, no licences,
nothing borrowed.

Details: [docs/testing-strategy.md](docs/testing-strategy.md).

---

## Documentation

| document | what's in it |
|---|---|
| [cli-and-outputs.md](docs/cli-and-outputs.md) | every flag, every file a run writes, exit codes, and what each output guarantees |
| [measurement-methodology.md](docs/measurement-methodology.md) | magnitude vs power, FFT, window, hop, frame sampling, bands |
| [calibration.md](docs/calibration.md) | how the thresholds were derived, and what that does and doesn't prove |
| [testing-strategy.md](docs/testing-strategy.md) | positive and negative cases, synthetic signals, cross-checks |
| [design-rules.md](docs/design-rules.md) | the hard rules the code follows, and the failure behind each one |
| [adding-profiles.md](docs/adding-profiles.md) | how to add a genre profile |
| [references.md](docs/references.md) | where each reference value comes from — including what is still undocumented |
| [ai-development-process.md](docs/ai-development-process.md) | how this was built with AI assistance |

---

## Limitations

Stated plainly, because a tool that judges should be judged back:

- **WAV only** (PCM 16/24/32-bit and IEEE float32). No MP3, no FLAC.
- **Two calibrated profiles.** Broad genre coverage is not the goal; calibrated coverage is.
- **BPM is an estimate**, reported as such and never used as a check.
- **The `fx-impact` thresholds are calibration baselines, not universal audio standards.**
  They were derived from a 105-sound reference set; independent validation against a
  separate labelled dataset is planned and has not been done. See
  [docs/calibration.md](docs/calibration.md).
- **The `techno-club` band thresholds are heuristics, not calibrated values.** Their
  provenance was traced in full on 2026-07-30: of its five values, the true-peak ceiling
  holds up against EBU R128 and the loudness window against a secondary source, one value
  was misread from its source, and two have no source at all. The profile is useful for
  catching gross problems and should not be quoted as a reference. The whole audit, with
  what each source actually says, is in [docs/references.md](docs/references.md).
- It evaluates measurable spectral and dynamic properties. It cannot tell you whether a
  sound is *good* — only whether it matches the reference you asked it to match.

---

## Authorship and AI assistance

Aisinestes was developed with substantial AI assistance.

The repository owner identified the original problem, defined the product requirements,
selected the expected outputs, tested the tool with real project audio, reviewed its
behaviour, requested corrections, and made the final product and calibration decisions.

Claude assisted heavily with implementation, technical research, test construction and
documentation.

This repository is presented as an AI-directed and human-validated development project,
not as evidence that every line was written manually by its owner. The process is
documented in [docs/ai-development-process.md](docs/ai-development-process.md).

## Licence

**[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)**
— see [LICENSE](LICENSE).

**Free for any noncommercial purpose.** Use it, study it, modify it, share it, build on it.
That explicitly includes personal use, research, hobby and amateur projects, and any use by
charitable, educational, public research, health, environmental or government organisations
— regardless of how they are funded.

**What it does not allow is commercial use**, including selling this software or a
derivative of it. If you want to use Aisinestes commercially, ask.

*Note: this project was initially published under the MIT licence and relicensed shortly
afterwards. Copies obtained under MIT keep the rights granted at that time; everything from
the relicensing commit onward is under the terms above. This is not an open-source licence
by the OSI definition, and the project does not claim to be one.*

The eleven test signals are generated by the included code and carry no third-party rights.
The `fx-impact` profile was calibrated by measuring sounds from
[Kenney's](https://kenney.nl/) public-domain library; no audio from it ships in this
repository.
