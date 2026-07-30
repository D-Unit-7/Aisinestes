# Calibration

A tool that judges is only worth as much as the references it judges against. This
document states where each threshold came from — and, just as importantly, what that
process does **not** prove.

## Calibration is not validation

This distinction matters and is easy to blur.

The `fx-impact` thresholds were **derived from** a 105-sound reference set. Reporting
afterwards that most of those same 105 sounds pass is **not independent validation** — it
is a consistency check against the set the thresholds were fitted to. Any threshold
derived from a set will, by construction, describe that set well.

So the honest claim is:

> The initial thresholds were derived from observable patterns and failure cases inside a
> 105-sound reference set.

And not:

> The thresholds were proven to identify good and bad impacts.

**These thresholds are calibration baselines.** Independent validation against a separate,
manually labelled dataset is planned and has not been done.

## Profiles are versioned

Every profile that judges anything carries a `version` string (`fx-impact` and
`techno-club` are both at **v1**), and it shows up in each report: `Genre: fx-impact
(profile v1)` in the text report, `"profile_version": "1"` in the JSON. The `none` profile
has no version — it judges nothing.

The version gets **bumped whenever any threshold in that profile moves**. Two reports
carrying different versions were not judged against the same references, and comparing
their verdicts without noticing that is exactly the kind of silent mistake this whole
document exists to prevent. The independent validation described below is expected to move
thresholds, so this number is expected to change.

## The reference set

**105 impact sounds** from [Kenney's public-domain library](https://kenney.nl/) (CC0):
21 families × 5 variants — metal, glass, wood, plate, bell, tin, punch, soft, mining and
others. The library's footsteps were deliberately excluded: those are steps, not impacts.

Two synthetic control signals sit alongside it, and both are part of the test suite:

| control | sub | body | bite | must |
|---|---|---|---|---|
| `fx_roto.wav` | 98.89 % | 0.06 % | 0.13 % | flag all three |
| `fx_bueno.wav` | 3.85 % | 11.63 % | 84.43 % | pass clean |

## How each threshold was placed

Every threshold sits in a **measured gap**, not on a round number picked by eye.

### Sub ceiling — 60 % (20–60 Hz)

The real impact with the most sub in the whole set is `impactMining_004` at **44.24 %**
(the next two are 32.04 % and 23.44 %). The broken control sits at **98.89 %**.

60 % lands almost exactly in the middle of the 44.24 → 98.89 gap: it lets genuinely
bass-heavy impacts through and still catches pure rumble.

### Body floor — 5 % (120–2000 Hz)

There is an empty gap in the data between the `impactSoft_heavy` family (**0.99–1.89 %**,
dull hits with no mids) and everything else, which starts at **11.11 %**
(`impactWood_heavy_002`) and continues at 12.28 % and 14.79 %.

The threshold is the geometric mean of that gap — √(1.89 × 11.11) = 4.58 — rounded to
**5 %**.

With it, 100 of the 105 pass, and so does the good control at 11.63 % (more than double
the threshold), while the broken control at 0.06 % flags. The five that don't pass are the
entire `impactSoft_heavy` family: muffled thuds with neither body nor edge. An "impact"
gate marking those is correct behaviour, not a false positive.

*This threshold used to be 15 %, a number inherited from the power convention. Under
magnitude it excluded the good control and eight entire Kenney families — a concrete
example of why changing the band convention forces a full recalibration.*

### Bite floor — 0.16 % (≥ 2000 Hz)

The dullest real impact in the set is `impactSoft_heavy_003` at **0.194 %**; the broken
control sits at **0.13 %**. The usable window is genuinely narrow, so the threshold is
again the geometric mean — √(0.13 × 0.194) = 0.159 — rounded to **0.16 %**.

**What this number does and does not mean.** Under the magnitude convention, low-level
high-frequency content weighs far more than it would under `|X|²`. So this check does *not*
judge how much edge a sound has; it only catches the pathological case of there being
essentially **nothing** above 2 kHz. It is the threshold most likely to move under
independent validation.

*It used to be 10 %, which under magnitude flagged 7 of the 12 professional impacts in the
first batch.*

### Attack — peak within the first 15 % of duration

The slowest of the 105 is `impactPunch_medium_001` at 0.075, i.e. half the threshold;
96 of the 105 come out at 0.000. The 15 % limit keeps twice the margin over the worst real
case, so it stays where the original specification put it.

## The `techno-club` profile

| check | reference |
|---|---|
| sub (20–60 Hz) | ≈ 22 % — accepted window 18–26 % |
| sub+bass (20–120 Hz) | 48–52 % |
| integrated loudness | −8 to −6 LUFS-I |
| loudness range | 5–8 LU |
| true peak | ≤ −1 dBFS |

The sub window is a symmetric ±4 percentage points around the single-point reference of
22 %, chosen to be the same order as the width of the sub+bass window (±2 around 50).

**These values were not calibrated the way `fx-impact` was, and they should not be read as
if they had been.** Their provenance was traced in full on 2026-07-30: they come from a
single web search whose result summary was never checked against the pages themselves. Of
the five, the true-peak ceiling holds up against EBU R128 and the loudness window against a
secondary source; the sub figure was misread from its source (22 % is an observed average
of amateur uploads, not a target); and the sub+bass window and the LRA range have no source
at all. The source also measures in a different convention over different bands, so its
figures are not transferable without a conversion that was never made.

No threshold was moved on that finding — importing the source's numbers would carry its
convention with them. Closing this properly means measuring a reference set of club masters
the way `fx-impact` was measured. The value-by-value audit is in
[references.md](references.md).

## The deliberately flawed reference sample

A known-flawed track is kept **locally** as a measurement reference:

| metric | measured | reference |
|---|---|---|
| sub (20–60 Hz) | 42.25 % | 18–26 % |
| sub+bass | 52.21 % | 48–52 % |
| integrated loudness | −22.70 LUFS | −8 to −6 |
| loudness range | 0.80 LU | 5–8 |

Four of its five checks flag, and that is the point. Its job is not to be a gold standard —
it is to prove the tool reproduces numbers that were measured by hand, independently,
before the tool existed: the manual measurement gave 42.9 % sub and the tool reports
42.25 % on the same file.

**Scope of this check, stated honestly:** the file is not distributed with this repository
and is **not part of the automated test suite**. Re-measuring it is a manual practice, not
a guard that runs on every commit. The automated equivalents are the two synthetic controls
above, which *are* in the harness. If you fork this project, the flawed-reference practice
is something you would have to set up with your own material.

## What would make this stronger

In rough order of value:

1. Keep the calibration files strictly separated from any validation files.
2. Obtain a second, independent collection of impact sounds.
3. Select a sample and label it manually (usable / broken) **before** running the tool.
4. Run the tool without touching any threshold.
5. Measure false positives and false negatives; document the results, including the
   thresholds that turn out to be wrong.

None of this is required to use the tool. All of it is required before claiming the
thresholds generalise.
