# Command line and outputs

What the tool accepts, what each run writes, and the guarantee every output makes. The
README shows the common path; this is the reference, including the parts that only matter
when you wire it into something automated.

## Invocation

```bash
python -m aisinestes <audio.wav> [--genre G] [--out DIR] [--brief]
python -m aisinestes <old.wav> <new.wav> --compare [--genre G] [--out DIR] [--brief]
```

| flag | default | what it does |
|---|---|---|
| `--genre` | `none` | profile to judge against: `fx-impact`, `techno-club`, or `none` (measures everything, judges nothing) |
| `--out` | `out` | base output folder. Each run gets its own subfolder inside it |
| `--brief` | off | print a short fixed-shape summary instead of the full report. **The same files are written either way** — this changes what goes to stdout, never what is produced |
| `--compare` | off | take two files, old first and new second, and report how every metric moved |

Any other combination of file count and `--compare` is rejected with a message, not
guessed at: one file with `--compare`, or three files without it, are mistakes worth
stopping for.

## What a run writes

```
<out>/<filename without extension>/
├── report.txt        the readable verdict
├── report.json       every raw metric, plus the verdict block
├── report.html       the same report as one self-contained page
├── spectrogram.png   log-frequency axis, labelled dB scale        (needs ffmpeg)
└── waveform.png      full waveform                                 (needs ffmpeg)
```

A comparison writes three files instead, and no images — a comparison is a table of
numbers, and each file can be run on its own to get its own images:

```
<out>/compare_<old>_vs_<new>/
├── compare.txt
├── compare.json
└── compare.html
```

When both files have the same name (the usual case: the same file before and after, from
two different folders) the folder becomes `compare_<name>_old_vs_<name>_new`, so the two
sides stay distinguishable in the one place a reader might publish.

`report.txt` and `report.json` carry the **absolute path** of the file that was measured.
That is deliberate — locally, you want to know exactly which file this was — and it is why
`out/` is not published. The two outputs designed to leave the machine, `--brief` and the
HTML page, never carry it.

## Exit codes

| code | meaning |
|:---:|---|
| `0` | everything measured, no flags |
| `1` | at least one flag — the audio was judged and it failed |
| `2` | no report could be produced (missing file, unsupported WAV, missing module) |
| `3` | no flags, but at least one metric was never measured |

**A flag outranks a missing metric.** A run with both returns `1`: if the audio was judged
and it failed, that is the news. Code `3` exists so a gate can tell *clean* from *clean as
far as it got*, and decide on purpose which of the two it accepts.

**Checks whose data is missing fail closed.** If the profile *checks* a metric that could
not be measured — `techno-club` checks loudness, so without `ffmpeg` three of its five
checks have no data — those checks come out `FLAG` and the run exits `1`, not `3`. A gate
never goes green, or even "incomplete", on something it was explicitly asked to judge and
could not. One consequence, documented here rather than discovered: **code `3` is
unreachable for `techno-club`.** It is reachable for `fx-impact`, whose four checks are all
spectral and whose loudness is measured but not judged.

**`--genre none` is not a gate.** With no profile there are no checks, the verdict word is
`NOT JUDGED`, and the exit code can only be `0`, `2` or `3` — never `1`. It measures; it
cannot fail. Using it in CI produces a green light that tested nothing.

## `--brief` — the output meant to be read by a machine

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

The checks that passed are left out on purpose. A summary that lists everything costs the
same to read as the report it summarises; what stays is what changes a decision — the
verdict, the failures, whatever went unmeasured, and where the full output landed.

Four guarantees, each of them tested:

- **Bounded.** At most twenty lines, at most 160 characters per line, one item per line.
  A reader knows in advance how much it is going to get.
- **Nothing disappears in silence.** Past the line cap, the entries that do not fit are
  *counted* in a final line that names the file holding all of them. A truncated list that
  looks complete is worse than a long one.
- **No absolute paths.** The audio is named by basename, the output folder is relativised,
  and the reasons `ffmpeg` gives — which contain paths of their own — are scrubbed before
  they go in. This is the output most likely to be pasted somewhere else.
- **`exit=N` is the real exit code**, printed as a line so it can be read without
  capturing the process status.

The verdict is one of four words:

| word | means |
|---|---|
| `CLEAN` | every check passed, everything measured |
| `FLAG` | at least one check failed |
| `INCOMPLETE` | nothing failed, but something was never measured |
| `NOT JUDGED` | the profile has no checks — nothing was tested |

`NOT JUDGED` exists because the alternative was a lie. With `--genre none` there are zero
checks and zero failures, and calling that `CLEAN` would claim a pass nobody ever tested
for. The HTML page uses the same word for the same case.

With `--compare`, `--brief` keeps the shape and swaps the body for one line per metric:

```
AISINESTES COMPARE <old> -> <new> | genre=fx-impact v1
VERDICT: CLEAN (0 of 4) | old: 3 FLAG of 4
<metric>: <old> -> <new> (<delta>) <direction> <transition>
files: <folder>/
exit=0
```

The verdict describes the **new** file only, because that is what the gate reads. The old
side goes out as a plain count: a comparison does not carry what the old run failed to
measure, so calling it clean would claim more than is known.

## `report.html` — the output meant to be handed to a person

Every run writes it; `--compare` writes `compare.html`. Same verdict, same tables, same
images, as one page:

- **Self-contained.** Inline CSS, PNGs embedded as data URIs, **zero external requests of
  any kind**. It opens the same on a machine with no network.
- **Basename only.** The page never carries the path the audio was measured from. That is
  what makes it the one output safe to send to somebody else.
- **Everything escaped.** File names reach the page as text, never as markup. The test
  suite proves it with a name built to break out (`a&b'c.wav`) and a negative case that
  fails if the raw name appears.
- **Byte-stable.** No generation timestamp. Two runs over the same file produce the same
  bytes, so a diff between two pages is about the audio and nothing else.

The page is a **derived** artifact: if the renderer is missing or throws, the failure is
recorded in `errors` — visible in `report.txt` and `report.json` — and the run continues.
It deliberately does not enter `unmeasured`, because failing to draw a page is not the
same as failing to know a number, and the exit code must not confuse the two.

## `--compare` — how direction is decided

Each metric comes out with a `direction` (`improved`, `worsened`, `unchanged`, or that it
cannot be judged) and, when the check has a threshold, a `transition` (`fixed`, `broke`,
`still failing`, `still passing`).

Direction reads the **meaning** of the check, not the sign of the difference: for a
ceiling, going down is better; for a floor, going up is better; for a range, getting
closer to it is better.

**Crossing the threshold outranks the size of the step.** If a check flipped between the
two files, the direction says so however small the delta was. This is not a detail: the
first version compared the raw numbers against a tolerance, and that tolerance swallowed
exactly the small-delta threshold crossings — which is precisely the case the feature was
built for.

Two cases that are reported rather than smoothed over: a metric the profile has no
reference for shows its real delta and says it cannot judge it; a side that was never
measured comes out `not comparable`, **never as a zero**.

**The comparison gates on the new file.** Its verdict is the exit code, with the same four
codes as a single run. The old file is context, not a vote.

## Profile version

Every profile carries a version, and it appears in all four outputs: `(profile v1)` in the
text report, `"profile_version": "1"` in the JSON, `v1` in the brief header and in the HTML
page. It is bumped whenever a threshold moves.

Without it, two reports produced months apart look comparable when they may have been
judged against different numbers. See [calibration.md](calibration.md) for how the
thresholds were derived in the first place. The `none` profile has no version, because it
has nothing to version.
