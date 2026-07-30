"""Per-genre references and metric evaluation.

This is where the thresholds an audio file gets judged against live, together with the
function that compares what was measured against them. Every value's derivation is
documented in docs/calibration.md; no genre and no reference gets added without one.

⚠️ CONVENTION: the per-band percentages split spectral MAGNITUDE |X|, not power |X|²
(settled by experiment — see docs/measurement-methodology.md). The fx-impact
thresholds are expressed in that convention and were recalibrated by measuring real
professional impacts — see the derivation of each one below. Changing the fft_bands
convention forces this calibration to be redone.

Project owner's rule, which outranks any formatting tidiness: the MEASURED number is
always shown exactly as it is. It does not get dressed up, and it never gets rounded
down to "0". If a value is so small that two decimals would print "0.00" while not
being zero, it goes out in scientific notation rather than lying with a zero.
"""

import math

# --- Aggregate bands ---------------------------------------------------------------
# The names are the ones in analyze.BANDS. All this declares is how they add up to
# form the groups the references work with.
BANDS_SUB = ("sub",)                               # 20-60 Hz
BANDS_SUB_BASS = ("sub", "bass")                   # 20-120 Hz
BANDS_BODY = ("low_mids", "mids")                  # 120-2000 Hz
BANDS_BITE = ("high_mids", "air")                  # 2000-16000 Hz

# Tolerance applied to the single-point sub reference for techno-club (22 %).
# The contract says "≈ 22 %", so a symmetric window of ±4 percentage points is opened,
# the same order as the width of the sub+bass window (48-52 %, ±2 around 50).
TECHNO_SUB_TOLERANCE = 4.0

GENRES = {
    "techno-club": {
        "description": "Techno meant for a club system: bass up front and high "
                       "loudness, but no digital clipping.",
        # Profile version. It gets BUMPED whenever any threshold below moves, so a report
        # can be told apart from one produced under different references. The pending
        # independent validation is expected to move them (see docs/calibration.md).
        "version": "1",
        # sub (<60 Hz) ≈ 22 % of the total energy. Window 18-26 % (22 ± 4).
        "sub_pct": (22.0 - TECHNO_SUB_TOLERANCE, 22.0 + TECHNO_SUB_TOLERANCE),
        # sub + bass (<120 Hz) between 48 and 52 % of the total energy: half the
        # spectrum sitting in the range that moves the air on the dance floor.
        "sub_bass_pct": (48.0, 52.0),
        # Integrated loudness between -8 and -6 LUFS: club master level.
        "lufs_i": (-8.0, -6.0),
        # Loudness range 5-8 LU: compressed, but not flattened into a straight line.
        "lra_lu": (5.0, 8.0),
        # True peak at most -1 dBFS: headroom so the lossy codec does not clip.
        "true_peak_max_db": -1.0,
    },
    # --------------------------------------------------------------------------
    # fx-impact: thresholds CALIBRATED on 27-jul against 105 professional impacts
    # (Kenney "impact-sounds" library, CC0, 21 families x 5 variants: metal, glass,
    # wood, plate, bell, tin, punch, soft, mining...). The footsteps of that library
    # were left out: those are steps, not impacts.
    # Every threshold sits in the measured GAP between the real impacts and the
    # broken control case, not on a round number picked by eye.
    # Control signals: fx_roto (sub 98.89 / body 0.06 / bite 0.13) has to flag all
    # three; fx_bueno (sub 3.85 / body 11.63 / bite 84.43) has to pass clean.
    # --------------------------------------------------------------------------
    "fx-impact": {
        "description": "Sound-effect hit/impact: it has to sound short, with body "
                       "and with edge, not like a sub rumble.",
        # Profile version: bumped whenever any threshold below moves (same rule as above).
        "version": "1",
        # Sub ceiling. Measured: the real impact with the MOST sub out of the 105 is
        # impactMining_004 at 44.24 % (next come 32.04 and 23.44). The broken case
        # sits at 98.89 %. 60 % lands almost in the middle of the 44.24 -> 98.89 gap:
        # it lets genuinely bass-heavy impacts through and still catches pure rumble.
        "sub_pct_max": 60.0,
        # Body floor (120-2000 Hz). Measured: there is an empty gap between the
        # impactSoft_heavy family (0.99 to 1.89 %, dull hits with no mids) and the
        # rest of the impacts, which starts at 11.11 % (impactWood_heavy_002) and
        # continues at 12.28 / 14.79. The threshold goes at the geometric mean of
        # that gap (sqrt of 1.89 x 11.11 = 4.58) rounded to 5 %. With this, 100 of
        # the 105 Kenney pass and so does fx_bueno (11.63 %, more than double the
        # threshold), while fx_roto (0.06 %) gets flagged. The 5 that do not pass are
        # the whole impactSoft_heavy family: they are muffled thuds with neither body
        # NOR edge, and an "impact" gate marking them is the correct behaviour, not a
        # false positive. It used to be 15 %, a number inherited from the power
        # convention: under magnitude that left out fx_bueno and 8 whole Kenney families.
        "body_pct_min": 5.0,
        # Bite floor (>=2000 Hz). Measured: the dullest real impact out of the 105
        # is impactSoft_heavy_003 at 0.194 %, and the broken case sits at 0.13 %. The
        # usable window is genuinely narrow, so the threshold goes at the geometric
        # mean (sqrt of 0.13 x 0.194 = 0.159) rounded to 0.16 %: the 105 Kenney pass
        # and fx_roto gets flagged.
        # What this number means: under the magnitude convention (not power), low-level
        # high-frequency content weighs far more than it does under |X|², so this check
        # does NOT judge "how much edge it has" — it only catches the pathological case
        # of there being literally NOTHING above 2 kHz. It used to be 10 %, which under
        # magnitude flagged 7 of the 12 professional impacts of the first batch.
        "bite_pct_min": 0.16,
        # Fast attack: the envelope peak has to land inside the first 15 % of the
        # duration. If the peak arrives later it is a swell, not an impact.
        # Measured: the slowest of the 105 Kenney is impactPunch_medium_001 at 0.075,
        # i.e. half the threshold; 96 of the 105 come out at 0.000. The contract's
        # 15 % keeps twice the margin over the worst real case, so it stays put.
        "attack_pos_max": 0.15,
    },
    # No genre: the metrics are reported and nothing is checked.
    "none": {},
}


def fmt_num(value, unit="", dec=2):
    """Format a number for display, in English (decimal point). The CLI uses it too.

    Never returns "0.00" for a value that is not zero: if rounding would flatten it,
    it switches to scientific notation. That is the whole point: the measurement is
    shown as it is.
    """
    if value is None:
        return "no data"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN" + (" " + unit if unit else "")
        if math.isinf(value):
            return ("-inf" if value < 0 else "+inf") + (" " + unit if unit else "")
    text = "%.*f" % (dec, value)
    if value != 0 and float(text) == 0:
        text = "%.3e" % value
    return text + (" " + unit if unit else "")


def _item(check, measured, target, ok):
    """Build one result item in the exact shape the contract asks for."""
    return {
        "check": check,
        "measured": measured,
        "target": target,
        "status": "OK" if ok else "FLAG",
    }


def _item_no_data(check, target):
    """Check that could not be evaluated because its input metric is missing.

    It is marked FLAG on purpose: a gate cannot go green on something that was never
    measured. The text spells out that the problem is the missing data, not a value
    out of range.
    """
    return _item(check, "no data (the metric never arrived)", target, False)


def _sum_bands(bands, names):
    """Add up the spectral magnitude percentage of a group of bands.

    Returns None if any band of the group is missing: better no data at all than a
    partial sum that would read as a real percentage.
    """
    if not isinstance(bands, dict):
        return None
    total = 0.0
    for name in names:
        value = bands.get(name)
        if value is None:
            return None
        total += float(value)
    return total


def _check_range(check, value, minimum, maximum, unit, dec=2, target_note=None):
    """Check of 'the value has to land between minimum and maximum' (both inclusive)."""
    target = "%s to %s" % (fmt_num(minimum, "", dec), fmt_num(maximum, unit, dec))
    if target_note:
        target = "%s  (%s)" % (target, target_note)
    if value is None:
        return _item_no_data(check, target)
    ok = minimum <= value <= maximum
    return _item(check, fmt_num(value, unit, dec), target, ok)


def _check_max(check, value, maximum, unit, dec=2):
    """Check of 'the value must not go above maximum'."""
    target = "<= %s" % fmt_num(maximum, unit, dec)
    if value is None:
        return _item_no_data(check, target)
    return _item(check, fmt_num(value, unit, dec), target, value <= maximum)


def _check_min(check, value, minimum, unit, dec=2):
    """Check of 'the value has to reach at least minimum'."""
    target = ">= %s" % fmt_num(minimum, unit, dec)
    if value is None:
        return _item_no_data(check, target)
    return _item(check, fmt_num(value, unit, dec), target, value >= minimum)


def evaluate(metrics, genre):
    """Compare the measured metrics against the genre references.

    metrics is the dictionary the CLI builds:
        {
          "bands":    {band_name: % of spectral magnitude},  # from analyze.fft_bands
          "loudness": {"lufs_i": f, "lra": f, "true_peak_db": f} | None,   # from ffreport
          "attack_pos": float | None,   # envelope peak position, 0.0 to 1.0
          ...  (every other key is ignored here)
        }

    Returns a list of items {"check", "measured", "target", "status"}.
    With genre="none" it returns an empty list: metrics only, no judgement.
    """
    if genre not in GENRES:
        raise ValueError(
            "Unknown genre: %r. Valid genres: %s"
            % (genre, ", ".join(sorted(GENRES)))
        )
    refs = GENRES[genre]
    if not refs:
        return []

    metrics = metrics or {}
    bands = metrics.get("bands") or {}
    loudness = metrics.get("loudness") or {}

    pct_sub = _sum_bands(bands, BANDS_SUB)
    pct_sub_bass = _sum_bands(bands, BANDS_SUB_BASS)
    pct_body = _sum_bands(bands, BANDS_BODY)
    pct_bite = _sum_bands(bands, BANDS_BITE)

    results = []

    if genre == "techno-club":
        minimum, maximum = refs["sub_pct"]
        results.append(_check_range(
            "Sub magnitude (20-60 Hz)", pct_sub, minimum, maximum, "%",
            target_note="reference ≈ 22 %",
        ))
        minimum, maximum = refs["sub_bass_pct"]
        results.append(_check_range(
            "Sub+bass magnitude (20-120 Hz)", pct_sub_bass, minimum, maximum, "%",
        ))
        minimum, maximum = refs["lufs_i"]
        results.append(_check_range(
            "Integrated loudness (LUFS-I)", loudness.get("lufs_i"), minimum, maximum, "LUFS",
        ))
        minimum, maximum = refs["lra_lu"]
        results.append(_check_range(
            "Loudness range (LRA)", loudness.get("lra"), minimum, maximum, "LU",
        ))
        results.append(_check_max(
            "True peak", loudness.get("true_peak_db"),
            refs["true_peak_max_db"], "dBFS",
        ))

    elif genre == "fx-impact":
        # The labels have to keep containing "Sub" and "Bite": the harness looks for
        # those substrings to verify that the broken impact flags where it should.
        results.append(_check_max(
            "Sub magnitude (20-60 Hz)", pct_sub, refs["sub_pct_max"], "%",
        ))
        results.append(_check_min(
            "Body magnitude (120-2000 Hz)", pct_body,
            refs["body_pct_min"], "%",
        ))
        results.append(_check_min(
            "Bite magnitude (2000 Hz and up)", pct_bite,
            refs["bite_pct_min"], "%",
        ))
        attack = metrics.get("attack_pos")
        limit = refs["attack_pos_max"]
        attack_target = "<= %s of duration (first %s %%)" % (
            fmt_num(limit, "", 2), fmt_num(limit * 100.0, "", 0),
        )
        if attack is None:
            results.append(_item_no_data(
                "Fast attack (envelope peak)", attack_target))
        else:
            results.append(_item(
                "Fast attack (envelope peak)",
                "%s of duration" % fmt_num(attack, "", 3),
                attack_target,
                attack <= limit,
            ))

    return results
