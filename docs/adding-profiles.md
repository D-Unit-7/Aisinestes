# Adding a profile

A profile is a genre of audio plus the references it gets judged against. Adding one is a
dictionary entry and a branch in the evaluator.

## 1. Declare the references

In `aisinestes/targets.py`, add an entry to `GENRES`:

```python
GENRES = {
    ...
    "your-profile": {
        "description": "One sentence, in plain language, describing what this kind of "
                       "audio is supposed to sound like. It is printed in the report.",
        "sub_pct": (18.0, 26.0),        # a range   -> checked with _check_range
        "body_pct_min": 5.0,            # a floor   -> checked with _check_min
        "sub_pct_max": 60.0,            # a ceiling -> checked with _check_max
        ...
    },
}
```

Write the derivation of every threshold in a comment next to it. Not the value — the
**derivation**: what was measured, what the gap was, why the number landed where it did.
Every existing threshold in that file does this, and it is the reason the calibration can
be audited at all.

## 2. Add the evaluation branch

In `evaluate()`, add a branch for the profile that turns each reference into a check:

```python
if genre == "your-profile":
    minimum, maximum = refs["sub_pct"]
    results.append(_check_range(
        "Sub magnitude (20-60 Hz)", pct_sub, minimum, maximum, "%",
        target_note="reference ≈ 22 %",
    ))
    results.append(_check_max(
        "Sub magnitude (20-60 Hz)", pct_sub, refs["sub_pct_max"], "%",
    ))
```

Helpers available: `_check_range`, `_check_min`, `_check_max`. Each returns an item with
`check`, `measured`, `target` and `status`, which is what both the text report and the
JSON consume — so you do not have to touch either.

Available aggregates: `pct_sub`, `pct_sub_bass`, `pct_body`, `pct_bite`, plus anything in
`metrics["loudness"]` and `metrics["attack_pos"]`.

## 3. Calibrate against real audio, not intuition

This is the part that matters, and it is the part that takes the time.

- Collect a set of **real, correctly-licensed** audio of that kind. The bigger and more
  professionally made, the better.
- Measure it all with `--genre none`, which reports every metric and judges nothing.
- Look for **gaps** in the distribution — the empty space between material that works and
  material that doesn't. Put the threshold in the gap, and record the numbers on both
  sides of it.
- Where a gap is narrow, the geometric mean of its edges is a reasonable placement and is
  what the `fx-impact` profile uses.
- Expect some of your reference set to flag. **A calibration where everything passes
  hasn't been calibrated; it's been fitted.** Check that the ones flagging are flagging for
  the reason the profile describes.

Mind the convention: all band percentages are shares of spectral **magnitude**, not power.
Published figures for a genre are frequently in the other convention, and the two differ by
more than a factor of two on real material. See
[measurement-methodology.md](measurement-methodology.md).

## 4. Add the tests, including the negative

Two fixtures at minimum, both generated from code in `harness/make_signals.py`:

- one that **must pass** the profile,
- one that **must flag** it, for a specific and stated reason.

Then add both directions to the harness: the passing file must pass, the failing file must
fail, and each expectation must also be run against the opposite file to confirm it fails
there. A detector that cannot be made to fail is not detecting anything — see
[testing-strategy.md](testing-strategy.md).

## 5. Document it

Add the profile to the table in the README, and add its derivation and sources to
[calibration.md](calibration.md) and [references.md](references.md). A threshold whose
origin is not written down becomes unauditable within weeks — `techno-club` is the
cautionary example in this repository.
