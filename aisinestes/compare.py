"""Comparison between two measurements of the same kind of audio: before and after.

Why this exists: a round of corrections on a track moved four metrics in the right
direction and pushed a fifth one the wrong way, and nobody saw it until the file was
measured again much later. A pass/fail verdict on the new file alone cannot show that.
The comparison can, so it is built to answer exactly one question per metric: did this
move towards its reference or away from it?

Three decisions hold the whole module up:

  - DIRECTION FOLLOWS THE SEMANTICS OF THE CHECK, not the sign of the difference. For a
    ceiling (`<= x`) going down is better; for a floor (`>= x`) going up is better; for
    a range going towards it is better. A raw "+11.57" says nothing on its own.
  - A SIDE THAT WAS NOT MEASURED IS NOT A ZERO. If either value is missing, the metric
    comes out `not_comparable` with a delta of None. Inventing a zero would turn a
    missing measurement into a fabricated improvement, which is the worst possible lie
    for a tool whose whole job is to be trusted about numbers.
  - THE GATE FOLLOWS THE NEW FILE. Comparing is not judging: the verdict is the verdict
    of the file that is being shipped, and the comparison only explains how it got there.

`not_comparable` covers exactly two situations, and they are told apart by the delta:

  - NO REFERENCE (target prints as "-"): the profile does not check this metric, so the
    move is reported with its real delta and no judgement — loudness under `fx-impact`,
    or anything at all under genre "none".
  - NO MEASUREMENT (delta is null): one of the two sides never produced a number, so
    there is no move to report either.
"""

import math
import os
import re

from aisinestes import analyze, pipeline
from aisinestes.targets import fmt_num

# A value counts as UNCHANGED when it moved less than this. Relative to where it came
# from (0.5 %), with an absolute floor (0.05) so that metrics that live near zero — a
# bite percentage of 0.13 %, an attack position of 0.000 — do not report a "change"
# every time the last decimal wobbles. The larger of the two wins.
EPS_REL = 0.005
EPS_ABS = 0.05

# Line budget of the comparison brief. Same numbers as the single-file brief, on purpose:
# both are read by the same machine and a different cap in each would be a trap.
BRIEF_MAX_LINES = 20
BRIEF_LINE_CHARS = 160

# Loudness metrics compared on top of the checks whenever BOTH sides have them. The
# labels are the ones targets.py emits, so that in a profile which does check them
# (techno-club) they are recognised as already present and not listed twice.
LOUDNESS_METRICS = (
    ("Integrated loudness (LUFS-I)", "lufs_i"),
    ("Loudness range (LRA)", "lra"),
    ("True peak", "true_peak_db"),
)

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _numbers(text):
    """Every number inside a formatted string, in order of appearance."""
    if text is None:
        return []
    return [float(match) for match in _NUMBER_RE.findall(str(text))]


def _measured_value(item):
    """The number behind the `measured` field of a check, or None.

    The checks carry their value already formatted ("98.89 %", "0.000 of duration",
    "-12.10 LUFS") and, when the metric never arrived, the words "no data ...". Reading
    the number back from there keeps a single source of truth: whatever targets.py
    decides to measure is what gets compared, with no second copy of the thresholds
    living in this module.
    """
    if not item:
        return None
    found = _numbers(item.get("measured"))
    return found[0] if found else None


def _semantics(target):
    """What kind of reference the target string describes.

    ("max", limit) for "<= 60.00 %", ("min", limit) for ">= 5.00 %",
    ("range", low, high) for "18.00 to 26.00 %", (None,) when there is no reference.
    """
    text = "" if target is None else str(target).strip()
    found = _numbers(text)
    if not found:
        return (None,)
    if text.startswith("<="):
        return ("max", found[0])
    if text.startswith(">="):
        return ("min", found[0])
    if " to " in text and len(found) >= 2:
        return ("range", found[0], found[1])
    return (None,)


def _comparable(value):
    """Whether a value can take part in a subtraction at all.

    A true peak of -inf is a real measurement (digital silence has no peak), so it is
    still SHOWN — but the difference between -inf and -0.1 dBFS is not a number anybody
    can act on, and printing "+inf" as a delta would be noise dressed as a result.
    """
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _distance_to_range(value, low, high):
    """How far a value is from the target window (0 while it is inside it)."""
    if value < low:
        return low - value
    if value > high:
        return value - high
    return 0.0


def _direction(old, new, semantics, transition="unknown"):
    """improved / worsened / unchanged / not_comparable, by the meaning of the check.

    Order of decision, and every step of it is there because of a way this got it wrong:

      1. NO REFERENCE first. A metric the profile does not check cannot be called better
         or worse at all, no matter how far it moved. Deciding this before looking at the
         delta is what stops the same metric from reading "unchanged" on a small move and
         "not comparable" on a big one.
      2. A SIDE MISSING is not comparable either, and it is the delta being None that
         tells the two cases apart.
      3. CROSSING THE THRESHOLD OUTRANKS THE EPSILON. If the check went from OK to FLAG
         or back, the value crossed the line the profile draws, and that IS the change —
         however small the step was. The epsilon exists to swallow noise, and it was
         bigger than the whole decision window of the small metrics: the attack lives in
         0 to 0.15 and the bite floor sits at 0.16 %, so a move of 0.05 that flipped the
         verdict was coming out as "unchanged" next to a transition of "broke". A
         direction that contradicts the gate is worse than no direction.
      4. Only with no crossing does the epsilon apply, and then the semantics.
    """
    kind = semantics[0]
    if not kind:
        return "not_comparable"
    if not _comparable(old) or not _comparable(new):
        return "not_comparable"
    if transition == "fixed":
        return "improved"
    if transition == "broke":
        return "worsened"
    delta = new - old
    if abs(delta) <= max(EPS_ABS, EPS_REL * abs(old)):
        return "unchanged"
    if kind == "max":
        return "improved" if delta < 0 else "worsened"
    if kind == "min":
        return "improved" if delta > 0 else "worsened"
    if kind == "range":
        before = _distance_to_range(old, semantics[1], semantics[2])
        after = _distance_to_range(new, semantics[1], semantics[2])
        if after < before:
            return "improved"
        if after > before:
            return "worsened"
        # Both inside the window: the profile has nothing to prefer between them.
        return "unchanged"
    return "not_comparable"


def _transition(item_old, item_new):
    """How the OK/FLAG status of a check moved. It is the gate's own reading.

    It is computed from the statuses exactly as they are, including the case where a
    check flags because its metric never arrived: that FLAG is real, the gate did flip,
    and the direction column is the one that says the number itself is not comparable.
    """
    before = (item_old or {}).get("status")
    after = (item_new or {}).get("status")
    if before not in ("OK", "FLAG") or after not in ("OK", "FLAG"):
        return "unknown"
    if before == "FLAG" and after == "OK":
        return "fixed"
    if before == "OK" and after == "FLAG":
        return "broke"
    return "still_flag" if after == "FLAG" else "still_ok"


def _metric(name, old, new, target, item_old=None, item_new=None):
    semantics = _semantics(target)
    delta = new - old if (_comparable(old) and _comparable(new)) else None
    transition = _transition(item_old, item_new)
    return {
        "name": name,
        "old": old,
        "new": new,
        "target": target if semantics[0] else "-",
        "delta": delta,
        "direction": _direction(old, new, semantics, transition),
        "transition": transition,
    }


def _side_labels(name_old, name_new):
    """Display names for the two files, disambiguated when the basename is the same.

    before/impact.wav and after/impact.wav are two different files with one basename, and
    every output of this module shows basenames only. Without this they read as the same
    file twice — the table, the page and the folder all become ambiguous. The parent
    folders are NOT the fix: they are exactly the part that must not travel, so the two
    sides get told apart by their ROLE instead.
    """
    if name_old and name_old == name_new:
        return "%s (old)" % name_old, "%s (new)" % name_new
    return name_old, name_new


def _by_check(data):
    return {item.get("check"): item for item in (data.get("checks") or [])}


def _flags(data):
    return sum(1 for item in (data.get("checks") or []) if item.get("status") == "FLAG")


def compare(data_old, data_new):
    """Compares two `data` dictionaries produced by pipeline.analyze_file.

    Both must have been measured against the same genre: comparing a file judged
    against one set of references with a file judged against another one produces a
    table that looks meaningful and is not, so it raises instead.
    """
    genre_old = (data_old or {}).get("genre")
    genre_new = (data_new or {}).get("genre")
    if genre_old != genre_new:
        raise ValueError(
            "the two files were measured against different genres (%r and %r): there is "
            "nothing to compare" % (genre_old, genre_new))

    checks_old = _by_check(data_old)
    checks_new = _by_check(data_new)

    # Order: the new file's checks first (it is the one being judged), then anything the
    # old one had and the new one does not.
    names = [item.get("check") for item in (data_new.get("checks") or [])]
    for item in (data_old.get("checks") or []):
        if item.get("check") not in names:
            names.append(item.get("check"))

    metrics = []
    for name in names:
        item_old = checks_old.get(name)
        item_new = checks_new.get(name)
        target = (item_new or item_old or {}).get("target")
        metrics.append(_metric(name, _measured_value(item_old), _measured_value(item_new),
                               target, item_old, item_new))

    # With no checks at all (genre "none") there is still something worth comparing: the
    # spectral balance. Nothing is judged, so no transitions come out of it.
    if not names:
        bands_old = data_old.get("bands") or {}
        bands_new = data_new.get("bands") or {}
        for band, low, high in analyze.BANDS:
            if band not in bands_old or band not in bands_new:
                continue
            metrics.append(_metric("Band %s (%d-%d Hz)" % (band, low, high),
                                   bands_old.get(band), bands_new.get(band), None))

    # Loudness on top, only when both sides have it and the profile does not already
    # check it (in techno-club these three ARE checks and are in the list above).
    loud_old = data_old.get("loudness") or {}
    loud_new = data_new.get("loudness") or {}
    for label, key in LOUDNESS_METRICS:
        if label in checks_old or label in checks_new:
            continue
        old_value = loud_old.get(key)
        new_value = loud_new.get(key)
        if old_value is None or new_value is None:
            continue
        metrics.append(_metric(label, float(old_value), float(new_value), None))

    name_old = os.path.basename(str(data_old.get("file") or ""))
    name_new = os.path.basename(str(data_new.get("file") or ""))
    label_old, label_new = _side_labels(name_old, name_new)

    return {
        # `file` is the bare basename, for machines. `label` is what the outputs print:
        # the same string, unless both sides share a basename and have to be told apart.
        "old": {"file": name_old, "label": label_old,
                "flags": _flags(data_old), "checks": len(data_old.get("checks") or [])},
        "new": {"file": name_new, "label": label_new,
                "flags": _flags(data_new), "checks": len(data_new.get("checks") or [])},
        "genre": genre_new,
        "profile_version": data_new.get("profile_version"),
        "metrics": metrics,
        # The gate result of the NEW file, so whoever reads compare.json can explain the
        # exit code without re-running anything. Same block as report.json's.
        "verdict": pipeline.build_verdict(data_new),
        # Failures of the DERIVED artifacts (compare.html above all). They are persisted
        # here for the same reason report.json keeps its own: a warning that only ever
        # went to stderr is a warning nobody reads twice. Nothing in this list can move
        # the exit code — the verdict above was already decided.
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _label(side):
    """What a side is called in the outputs: its label, or the bare basename."""
    side = side or {}
    return str(side.get("label") or side.get("file") or "?")


def missing_sides(cmp):
    """Metrics where one of the two sides has no number, with which side is missing.

    Returns [(metric name, "old"|"new"|"both"), ...]. Every output that a person reads
    has to be able to say this out loud: a comparison that quietly drops a metric looks
    like a comparison where nothing happened.
    """
    out = []
    for metric in cmp.get("metrics") or []:
        has_old = metric.get("old") is not None
        has_new = metric.get("new") is not None
        if has_old and has_new:
            continue
        out.append((str(metric.get("name")),
                    "both" if not has_old and not has_new
                    else ("old" if not has_old else "new")))
    return out


def _cell(value, dec=3):
    return "not measured" if value is None else fmt_num(value, "", dec)


def _delta_cell(value, dec=3):
    if value is None:
        return "-"
    text = fmt_num(value, "", dec)
    return "+" + text if value > 0 else text


def render_compare_txt(cmp):
    """Readable table of a comparison, in the shape of the rest of the reports."""
    old = cmp.get("old") or {}
    new = cmp.get("new") or {}
    verdict = cmp.get("verdict") or {}
    width = 74

    lines = ["Aisinestes — comparison", "=" * width,
             "Old:   %-28s %d FLAG of %d checks"
             % (_label(old), old.get("flags", 0), old.get("checks", 0)),
             "New:   %-28s %d FLAG of %d checks"
             % (_label(new), new.get("flags", 0), new.get("checks", 0))]
    genre = cmp.get("genre")
    version = cmp.get("profile_version")
    lines.append("Genre: %s" % ("%s (profile v%s)" % (genre, version) if version
                                else genre))
    lines.append("")

    rows = [("metric", "old", "new", "delta", "direction", "transition", "target")]
    for metric in cmp.get("metrics") or []:
        rows.append((
            str(metric.get("name")),
            _cell(metric.get("old")), _cell(metric.get("new")),
            _delta_cell(metric.get("delta")),
            str(metric.get("direction")), str(metric.get("transition")),
            str(metric.get("target") or "-"),
        ))
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    for index, row in enumerate(rows):
        lines.append("  " + "  ".join(row[col].ljust(widths[col])
                                      for col in range(len(row))).rstrip())
        if index == 0:
            lines.append("  " + "  ".join("-" * widths[col] for col in range(len(row))))

    lines.append("")
    lines.append("  The gate follows the NEW file: %d FLAG of %d checks (exit %d)."
                 % (verdict.get("flags", 0), verdict.get("checks", 0),
                    verdict.get("exit_code", pipeline.EXIT_NO_REPORT)))
    for name, side in missing_sides(cmp):
        lines.append("  NOT MEASURED on the %s file: %s" % (side, name))
    for entry in verdict.get("unmeasured") or []:
        lines.append("  NOT MEASURED on the new file: %s" % entry)
    for entry in cmp.get("errors") or []:
        lines.append("  ERROR: %s" % entry)
    lines.append("")
    lines.append("  direction reads the semantics of each check, not the sign: for a")
    lines.append("  ceiling going down is better, for a floor going up is better, for a")
    lines.append("  range getting closer to it is better. Crossing the threshold outranks")
    lines.append("  the epsilon: if the check flipped, the direction says so however small")
    lines.append("  the step was.")
    lines.append("  'not_comparable' covers two cases, told apart by the delta: target '-'")
    lines.append("  means the profile has no reference for that metric (the delta is real,")
    lines.append("  the judgement is not), and a delta of '-' means the difference is")
    lines.append("  not a number — a side was never measured, or it is not finite")
    lines.append("  (digital silence has a true peak of -inf). Neither is ever a zero.")
    lines.append("")
    return "\n".join(lines)


def _folder_text(out_folder):
    """The output folder as it goes into the brief: never an absolute path.

    The CLI hands over a path that is already relative; this is the guard for any other
    caller. It deliberately does NOT run scrub_paths, which is built for absolute paths
    embedded in prose and would eat the first segment of a relative one.
    """
    text = str(out_folder).replace(os.sep, "/").rstrip("/")
    if re.match(r"^(?:[A-Za-z]:|/)", text):
        parts = [p for p in text.split("/") if p]
        if len(parts) >= 2:
            text = ".../" + "/".join(parts[-2:])
        else:
            text = parts[-1] if parts else "."
    return text + "/"


def _brief_line(text):
    """Collapses a value to one line and cuts it, so the format stays parseable."""
    text = " ".join(str(text).split())
    if len(text) > BRIEF_LINE_CHARS:
        text = text[:BRIEF_LINE_CHARS - 4] + " ..."
    return text


def _verdict_word(verdict, genre):
    """The same four words the single-file brief uses, with the same counts.

    NOT JUDGED is not a nicety: with no checks there is no pass to report, and calling
    that CLEAN would claim one. The two briefs have to agree on the vocabulary or a
    machine cannot read them with the same parser.
    """
    flags = verdict.get("flags", 0)
    total = verdict.get("checks", 0)
    unmeasured = verdict.get("unmeasured") or []
    if flags:
        return "FLAG (%d of %d)" % (flags, total)
    if unmeasured:
        return "INCOMPLETE (0 of %d, %d unmeasured)" % (total, len(unmeasured))
    if not total:
        return 'NOT JUDGED (0 checks: genre "%s")' % genre
    return "CLEAN (0 of %d)" % total


def render_compare_brief(cmp, out_folder):
    """One line per metric, fixed shape, for whoever reads output by the line.

        AISINESTES COMPARE <old> -> <new> | genre=fx-impact v1
        VERDICT: CLEAN (0 of 4) | old: 3 FLAG of 4
        <metric>: <old> -> <new> (<delta>) <direction> <transition>
        UNMEASURED loudness (ebur128): <reason>      # only if there is one
        files: <folder>/
        exit=<code>

    The verdict describes the NEW file only, because that is what the gate reads; the old
    side goes out as a plain count, since a comparison does not carry what the old run
    failed to measure and "CLEAN" would be claiming more than is known.

    Two rules the single-file brief already follows and this one has to match: nothing is
    silently dropped (past the line cap the rest is COUNTED, and the file that has it all
    is named), and no line carries an absolute path — the reasons inside UNMEASURED come
    straight from ffmpeg and those do contain paths, so they are scrubbed. The `exit=`
    line is the code the process really returns.
    """
    old = cmp.get("old") or {}
    new = cmp.get("new") or {}
    verdict = cmp.get("verdict") or {}
    genre = "genre=%s" % cmp.get("genre")
    version = cmp.get("profile_version")
    if version:
        genre += " v%s" % version

    lines = ["AISINESTES COMPARE %s -> %s | %s" % (_label(old), _label(new), genre),
             "VERDICT: %s | old: %d FLAG of %d"
             % (_verdict_word(verdict, cmp.get("genre")),
                old.get("flags", 0), old.get("checks", 0))]

    details = []
    for metric in cmp.get("metrics") or []:
        transition = metric.get("transition")
        details.append("%s: %s -> %s (%s) %s%s" % (
            metric.get("name"), _cell(metric.get("old")), _cell(metric.get("new")),
            _delta_cell(metric.get("delta")), metric.get("direction"),
            "" if transition in (None, "unknown") else " " + transition))
    details += ["UNMEASURED %s" % pipeline.scrub_paths(entry)
                for entry in verdict.get("unmeasured") or []]
    details += ["ERROR %s" % pipeline.scrub_paths(entry)
                for entry in cmp.get("errors") or []]
    details = [_brief_line(line) for line in details]

    # Fixed lines: header, verdict, files, exit. Whatever does not fit is counted, never
    # silently dropped.
    room = BRIEF_MAX_LINES - 4
    if len(details) > room:
        hidden = len(details) - (room - 1)
        details = details[:room - 1] + ["... (+%d more, see compare.txt)" % hidden]

    lines += details
    lines.append("files: %s" % _folder_text(out_folder))
    lines.append("exit=%d" % verdict.get("exit_code", pipeline.EXIT_NO_REPORT))
    return "\n".join(lines)
