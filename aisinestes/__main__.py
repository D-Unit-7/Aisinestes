"""Aisinestes — CLI: measures a WAV and writes a readable report + images.

Usage:
    python -m aisinestes <audio.wav> [--genre techno-club|fx-impact|none] [--out out]
                         [--brief]
    python -m aisinestes <old.wav> <new.wav> --compare [--genre ...] [--out out] [--brief]

Generates, inside <out>/<file name without extension>/:
    report.json       full raw metrics
    report.txt        human-readable report
    report.html       the same report, self-contained and shareable (basename only)
    spectrogram.png   spectrogram (logarithmic frequency axis, with legend)
    waveform.png      full waveform

--brief writes exactly the same files and prints a short fixed-shape summary instead of
the full report: the verdict, the checks that failed and whatever went unmeasured.

--compare takes TWO files (old first, new second) and writes, inside
<out>/compare_<old>_vs_<new>/, compare.txt, compare.json and compare.html: how every
metric moved and whether that move was towards its reference or away from it. It gates
on the NEW file — its verdict is the exit code.

Exit codes (meant for using it as a gate):
    0  everything measured and not a single FLAG
    1  there is at least one FLAG (the audio was judged and it failed)
    2  the report could not be produced (missing file, unsupported WAV, missing module)
    3  zero FLAG but at least one metric was never measured (INCOMPLETE verdict,
       e.g. loudness without ffmpeg). A FLAG outranks this: 1 wins over 3.
"""

import argparse
import json
import os
import sys

# When run with `python -m aisinestes` the package is already on the path; this also covers
# the case of executing the file directly from another folder.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from aisinestes import ffreport, targets
from aisinestes.targets import fmt_num

# The pipeline pulls in wavio and analyze. If one of those modules is not there we do not
# want an import traceback at startup: main() turns it into a clear message and code 2.
try:
    from aisinestes import pipeline
    PIPELINE_IMPORT_ERROR = None
except ImportError as error:
    pipeline = None
    PIPELINE_IMPORT_ERROR = error

# The HTML report is a DERIVED artifact: if this module is not there, the run loses the
# page and nothing else. It is imported apart from the pipeline for that exact reason —
# a missing renderer must not be able to hold back a measurement.
try:
    from aisinestes import htmlreport
    HTMLREPORT_IMPORT_ERROR = None
except ImportError as error:
    htmlreport = None
    HTMLREPORT_IMPORT_ERROR = error

# The comparison, on the other hand, IS the job when --compare is asked for: without it
# there is nothing to produce, and that is a code 2.
try:
    from aisinestes import compare as compare_mod
    COMPARE_IMPORT_ERROR = None
except ImportError as error:
    compare_mod = None
    COMPARE_IMPORT_ERROR = error

WIDTH = 74

# Ceiling for --brief, so a machine reading it knows how much it is going to get.
BRIEF_MAX_LINES = 20
# Any single brief line is collapsed and cut to this width: an ffmpeg error can come with
# several lines inside it and the format is one item per line.
BRIEF_LINE_CHARS = 160


def _force_utf8_console():
    """The Windows console usually comes up as cp1252, and '≈' or '≤' do not fit there.

    The output is reconfigured to UTF-8 so the report can be printed exactly as it is
    written to the file. If the Python version or the stream does not allow it, we carry
    on anyway: the files come out in UTF-8 no matter what.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _rule(title):
    """Section separator of the text report."""
    padding = "-" * max(3, WIDTH - len(title) - 3)
    return "-- %s %s" % (title, padding)


def _references_per_band(genre):
    """Reference text for each band, derived from targets.GENRES.

    It is built from the real thresholds so that there are not two sources of truth:
    if a number is touched in targets.py, the report table follows on its own.
    Bands with no researched reference get a '-' (none is ever made up).
    """
    ref = targets.GENRES.get(genre) or {}
    out = {}
    if genre == "techno-club":
        minimum, maximum = ref["sub_pct"]
        out["sub"] = "≈ 22 %% (%s to %s)" % (fmt_num(minimum), fmt_num(maximum, "%"))
    elif genre == "fx-impact":
        out["sub"] = "<= %s" % fmt_num(ref["sub_pct_max"], "%")
        body_text = "body share (>= %s)" % fmt_num(ref["body_pct_min"], "%")
        bite_text = "bite share (>= %s)" % fmt_num(ref["bite_pct_min"], "%")
        for band in targets.BANDS_BODY:
            out[band] = body_text
        for band in targets.BANDS_BITE:
            out[band] = bite_text
    return out


def _bands_table(bands_def, measured_bands, genre):
    """Table 'band / range Hz / measured / reference'."""
    references = _references_per_band(genre)
    rows = [("band", "range Hz", "% magnitude", "reference")]
    for name, hz_min, hz_max in bands_def:
        measured = measured_bands.get(name)
        rows.append((
            name,
            "%d-%d" % (hz_min, hz_max),
            fmt_num(measured, "%") if measured is not None else "no data",
            references.get(name, "-"),
        ))
    widths = [max(len(row[col]) for row in rows) for col in range(4)]
    lines = []
    for index, row in enumerate(rows):
        lines.append("  " + "  ".join(
            row[col].ljust(widths[col]) for col in range(4)
        ).rstrip())
        if index == 0:
            lines.append("  " + "  ".join("-" * widths[col] for col in range(4)))
    numeric = [v for v in measured_bands.values() if isinstance(v, (int, float))]
    lines.append("")
    if numeric:
        lines.append("  band sum: %s (sanity check: it has to come out ~100 %%)"
                     % fmt_num(sum(numeric), "%"))
    else:
        lines.append("  band sum: no data (empty spectrum: band shares are undefined)")
    return "\n".join(lines)


def _checks_table(checks):
    """Table of OK/FLAG checks against the genre references."""
    rows = [("status", "check", "measured", "target")]
    for item in checks:
        rows.append((item["status"], item["check"], item["measured"], item["target"]))
    widths = [max(len(row[col]) for row in rows) for col in range(4)]
    lines = []
    for index, row in enumerate(rows):
        lines.append("  " + "  ".join(
            row[col].ljust(widths[col]) for col in range(4)
        ).rstrip())
        if index == 0:
            lines.append("  " + "  ".join("-" * widths[col] for col in range(4)))
    return "\n".join(lines)


def _result_line(flag_count, check_count, unmeasured):
    """The RESULT line of the text report, with the INCOMPLETE suffix when it applies.

    A clean verdict on a report with a metric missing is not the same as a clean verdict
    on a complete one, so the line says which one it is instead of leaving the reader to
    go dig in the Errors section.
    """
    labels = pipeline.unmeasured_labels(unmeasured)
    if flag_count:
        text = "RESULT: %d FLAG of %d checks" % (flag_count, check_count)
    elif labels:
        text = "RESULT: 0 FLAG of %d checks" % check_count
    else:
        return "RESULT: %d checks, none flagged." % check_count
    if labels:
        return "%s — INCOMPLETE: %s not measured." % (text, ", ".join(labels))
    return text + "."


def build_report_txt(data, checks, bands_def):
    """Builds the human-readable report from the already computed metrics."""
    signal = data["signal"]
    parts = []
    parts.append("Aisinestes — report")
    parts.append("=" * WIDTH)
    parts.append("File:  %s" % data["file"])
    version = data.get("profile_version")
    if version:
        parts.append("Genre: %s (profile v%s)" % (data["genre"], version))
    else:
        parts.append("Genre: %s" % data["genre"])
    description = (targets.GENRES.get(data["genre"]) or {}).get("description")
    if description:
        parts.append("       %s" % description)
    parts.append("")

    parts.append(_rule("Signal"))
    parts.append("  Duration:       %s" % fmt_num(signal["duration"], "s", 3))
    parts.append("  Sample rate:    %d Hz" % signal["rate"])
    parts.append("  Channels:       %d" % signal["channels"])
    parts.append("  Bits:           %d" % signal["bits"])
    parts.append("  Peak:           %s  (%s)" % (
        fmt_num(signal["peak"], "", 4), fmt_num(signal["peak_db"], "dBFS")))
    parts.append("  RMS:            %s" % fmt_num(signal["rms_db"], "dBFS"))
    parts.append("  DC offset:      %s" % fmt_num(signal["dc_offset"], "", 6))
    parts.append("")

    parts.append(_rule("Loudness (EBU R128 via ffmpeg)"))
    loudness = data.get("loudness")
    if loudness:
        parts.append("  LUFS-I:         %s" % fmt_num(loudness["lufs_i"], "LUFS"))
        parts.append("  LRA:            %s" % fmt_num(loudness["lra"], "LU"))
        parts.append("  True peak:      %s" % fmt_num(loudness["true_peak_db"], "dBFS"))
    else:
        parts.append("  COULD NOT MEASURE: %s" % data.get("loudness_error", "unknown reason"))
    parts.append("")

    parts.append(_rule("Spectral distribution"))
    parts.append("  (spectrum averaged over frames — Hann window 8192, hop 4096; on")
    parts.append("   long files up to ~200 evenly spread frames are sampled)")
    parts.append("  (split by spectral MAGNITUDE |X|, not by power |X|²: the")
    parts.append("   percentages and every reference use that convention)")
    parts.append("")
    parts.append(_bands_table(bands_def, data["bands"], data["genre"]))
    parts.append("")

    parts.append(_rule("Transients"))
    onsets = data.get("onsets") or {}
    parts.append("  Onsets detected:   %s" % onsets.get("count", "no data"))
    bpm = onsets.get("bpm")
    parts.append("  BPM estimate:      %s" % (fmt_num(bpm, "BPM", 1) if bpm else "not determined"))
    if data.get("attack_pos") is not None:
        parts.append("  Envelope peak:     %s of duration (%s)" % (
            fmt_num(data["attack_pos"], "", 3), fmt_num(data.get("attack_s"), "s", 3)))
    times = onsets.get("times") or []
    if times:
        sample = ", ".join(fmt_num(t, "", 3) for t in times[:12])
        if len(times) > 12:
            sample += ", ... (%d in total)" % len(times)
        parts.append("  Times (s):         %s" % sample)
    parts.append("")

    parts.append(_rule("Checks"))
    if data["genre"] == "none":
        parts.append("  Genre 'none': metrics only, no checks and no references.")
    elif not checks:
        parts.append("  No checks for this genre.")
    else:
        parts.append(_checks_table(checks))
        flag_count = sum(1 for c in checks if c["status"] == "FLAG")
        parts.append("")
        parts.append("  " + _result_line(flag_count, len(checks), data.get("unmeasured")))
    parts.append("")

    errors = data.get("errors") or []
    if errors:
        parts.append(_rule("Errors"))
        for error in errors:
            parts.append("  - %s" % error)
        parts.append("")

    parts.append(_rule("Images"))
    for key, path in (data.get("images") or {}).items():
        parts.append("  %-14s %s" % (key + ":", path))
    parts.append("")
    return "\n".join(parts)


def _brief_line(text):
    """Collapses a value to a single line and cuts it, so the format stays parseable."""
    text = " ".join(str(text).split())
    if len(text) > BRIEF_LINE_CHARS:
        text = text[:BRIEF_LINE_CHARS - 4] + " ..."
    return text


def _brief_folder(out_folder):
    """The output folder as it goes into the brief: relative if it is below the current
    directory, scrubbed to its last components otherwise. The brief never carries an
    absolute path — it is the output most likely to be pasted somewhere else; the full
    location is always available in report.txt/report.json, which stay local."""
    try:
        relative = os.path.relpath(out_folder, os.getcwd())
    except ValueError:
        # Different drive on Windows: there is no relative path to give.
        relative = ""
    if not relative or relative.startswith(".."):
        parts = [p for p in str(out_folder).replace("\\", "/").split("/") if p]
        relative = ".../" + "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else ".")
    return relative.replace(os.sep, "/").rstrip("/") + "/"


def build_brief(data, out_folder):
    """Short fixed-shape summary of the report, for whoever reads output by the line.

    Only what changes a decision goes in: the verdict, the checks that FAILED and the
    metrics that could not be measured. The OK checks are left out on purpose — that is
    exactly what makes this cheap to read. The shape is:

        AISINESTES <basename> | genre=<genre> v<version> | <duration>s <rate>Hz
        VERDICT: FLAG (3 of 4)          # or CLEAN (0 of 4) / INCOMPLETE (0 of 5, 1 unmeasured)
        FLAG <check>: <measured> vs <target>
        UNMEASURED <what>: <reason>
        files: <folder>/
        exit=<code>

    The `exit=` line is the same number the process returns: it comes out of the verdict
    block, which is what decides the exit code.
    """
    signal = data["signal"]
    verdict = data.get("verdict") or pipeline.build_verdict(data)
    unmeasured = verdict.get("unmeasured") or []
    flags = verdict.get("flags", 0)
    total = verdict.get("checks", 0)

    genre = "genre=%s" % data["genre"]
    version = data.get("profile_version")
    if version:
        genre += " v%s" % version
    # Only the basename: the brief is the output most likely to be pasted somewhere else.
    # fmt_num is used for the duration so an absurdly short file does not read as "0.000s"
    # (the house rule about never rounding a measurement down to zero); the space it puts
    # before the unit is dropped, the field is "0.500s".
    lines = ["AISINESTES %s | %s | %s %dHz" % (
        os.path.basename(data["file"]), genre,
        fmt_num(signal["duration"], "s", 3).replace(" ", ""), signal["rate"])]

    if flags:
        lines.append("VERDICT: FLAG (%d of %d)" % (flags, total))
    elif unmeasured:
        lines.append("VERDICT: INCOMPLETE (0 of %d, %d unmeasured)" % (total, len(unmeasured)))
    elif total == 0:
        # Genre "none" judges nothing. Calling that CLEAN would claim a pass nobody
        # ever tested for — the html page says NOT JUDGED and this line has to agree.
        lines.append("VERDICT: NOT JUDGED (0 checks: genre \"%s\")" % data["genre"])
    else:
        lines.append("VERDICT: CLEAN (0 of %d)" % total)

    details = ["FLAG %s: %s vs %s" % (item["check"], item["measured"], item["target"])
               for item in (data.get("checks") or []) if item["status"] == "FLAG"]
    # The reasons inside UNMEASURED entries can carry ffmpeg's own words, absolute paths
    # included — and the brief is the output most likely to be pasted somewhere else.
    details += ["UNMEASURED %s" % pipeline.scrub_paths(entry) for entry in unmeasured]
    details = [_brief_line(line) for line in details]

    # Fixed lines: header, verdict, files, exit. Whatever does not fit is counted, never
    # silently dropped.
    room = BRIEF_MAX_LINES - 4
    if len(details) > room:
        hidden = len(details) - (room - 1)
        details = details[:room - 1] + ["... (+%d more, see report.txt)" % hidden]

    lines += details
    lines.append("files: %s" % _brief_folder(out_folder))
    lines.append("exit=%d" % verdict.get("exit_code", pipeline.EXIT_NO_REPORT))
    return "\n".join(lines)


def write_report_html(data, checks, bands_def, html_path):
    """Writes report.html. A failure here is recorded and changes nothing else.

    The page is a DERIVED artifact, not a measurement: it goes into `errors` (so it is
    visible in report.json and report.txt) and deliberately NOT into `unmeasured`, which
    is the list that can hold the verdict back. Not being able to draw a page is not the
    same as not knowing a number, and the exit code must not confuse the two — which is
    why this runs after the verdict has already been decided.
    """
    try:
        if htmlreport is None:
            raise RuntimeError("the htmlreport module is not available (%s)"
                               % HTMLREPORT_IMPORT_ERROR)
        text = htmlreport.build_report_html(data, checks, bands_def)
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return True
    except Exception as error:
        data["errors"].append("report.html: %s" % error)
        return False


def run_compare(args):
    """--compare: measures two files and reports how each metric moved between them.

    The gate follows the NEW file. The comparison explains the change; the verdict of
    the file that is being shipped is what the exit code says.
    """
    if compare_mod is None:
        print("ERROR: the comparison module is missing (%s)." % COMPARE_IMPORT_ERROR,
              file=sys.stderr)
        return pipeline.EXIT_NO_REPORT

    paths = []
    for given in args.audio:
        path = os.path.abspath(given)
        if not os.path.isfile(path):
            print("ERROR: the audio file does not exist: %s" % path, file=sys.stderr)
            return pipeline.EXIT_NO_REPORT
        paths.append(path)
    old_path, new_path = paths

    try:
        data_old = pipeline.analyze_file(old_path, args.genre)
        data_new = pipeline.analyze_file(new_path, args.genre)
    except Exception as error:
        print("ERROR reading the WAV: %s" % error, file=sys.stderr)
        return pipeline.EXIT_NO_REPORT

    # No images here: a comparison is a table of numbers, and the three files it writes
    # are the three the reader needs. Each file can be run on its own to get its images.
    try:
        comparison = compare_mod.compare(data_old, data_new)
    except Exception as error:
        print("ERROR building the comparison: %s" % error, file=sys.stderr)
        return pipeline.EXIT_NO_REPORT

    # Two files can share a basename (before/impact.wav vs after/impact.wav). The folder
    # tells them apart by their ROLE, never by the parent directory: that is exactly the
    # part that must not end up in a name somebody publishes.
    stems = [os.path.splitext(os.path.basename(path))[0] for path in paths]
    if stems[0] == stems[1]:
        folder_name = "compare_%s_old_vs_%s_new" % (stems[0], stems[1])
    else:
        folder_name = "compare_%s_vs_%s" % (stems[0], stems[1])
    out_folder = os.path.join(os.path.abspath(args.out), folder_name)
    try:
        os.makedirs(out_folder, exist_ok=True)
    except OSError as error:
        print("ERROR: the output folder could not be created (%s): %s"
              % (out_folder, error), file=sys.stderr)
        return pipeline.EXIT_NO_REPORT

    # Same rule as report.html: the page is derived, so a failure is recorded and the
    # comparison still gets written. It cannot change the exit code — the verdict inside
    # `comparison` was decided before this line. The failure goes into compare.json's
    # `errors` (and from there into compare.txt), not only to stderr: a warning that
    # scrolled past once is a warning nobody can check afterwards.
    html_text = None
    try:
        if htmlreport is None:
            raise RuntimeError("the htmlreport module is not available (%s)"
                               % HTMLREPORT_IMPORT_ERROR)
        html_text = htmlreport.build_compare_html(comparison)
    except Exception as error:
        entry = "compare.html: %s" % error
        comparison.setdefault("errors", []).append(entry)
        print("WARNING: %s" % entry, file=sys.stderr)

    # Rendered after the HTML attempt so that a failure above shows up in the table too.
    text = compare_mod.render_compare_txt(comparison)

    try:
        with open(os.path.join(out_folder, "compare.json"), "w", encoding="utf-8") as fh:
            json.dump(comparison, fh, ensure_ascii=False, indent=2)
        with open(os.path.join(out_folder, "compare.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        if html_text is not None:
            with open(os.path.join(out_folder, "compare.html"), "w",
                      encoding="utf-8") as fh:
                fh.write(html_text)
    except OSError as error:
        print("ERROR writing the comparison to %s: %s" % (out_folder, error),
              file=sys.stderr)
        return pipeline.EXIT_NO_REPORT

    if args.brief:
        print(compare_mod.render_compare_brief(comparison, _brief_folder(out_folder)))
    else:
        print(text)
        print("Comparison written to: %s" % out_folder)

    return comparison["verdict"]["exit_code"]


def main(argv=None):
    _force_utf8_console()
    parser = argparse.ArgumentParser(
        prog="python -m aisinestes",
        description="Measures a WAV file and writes metrics, per-genre checks and images.",
    )
    parser.add_argument(
        "audio", nargs="+", metavar="audio",
        help="path to the WAV file to analyse; with --compare, two of them: old then new",
    )
    parser.add_argument(
        "--genre", default="none", choices=sorted(targets.GENRES),
        help="genre to evaluate against (default: none, metrics only)",
    )
    parser.add_argument(
        "--out", default="out",
        help="base output folder (default: out)",
    )
    parser.add_argument(
        "--brief", action="store_true",
        help="print a short fixed-shape summary instead of the full report "
             "(the same files are written either way)",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="compare two files: old.wav new.wav --compare. Reports how every metric "
             "moved and gates on the NEW file",
    )
    args = parser.parse_args(argv)

    # Exactly two files with --compare, exactly one without it. Any other combination is
    # ambiguous about which file is being judged, and a gate cannot afford that.
    if args.compare and len(args.audio) != 2:
        parser.error("--compare needs exactly two files, old first and new second "
                     "(%d given)" % len(args.audio))
    if not args.compare and len(args.audio) != 1:
        parser.error("only one file is analysed at a time (%d given); to compare two "
                     "of them add --compare" % len(args.audio))

    if pipeline is None:
        print("ERROR: modules of the aisinestes package are missing (%s)."
              % PIPELINE_IMPORT_ERROR, file=sys.stderr)
        print("aisinestes/wavio.py and aisinestes/analyze.py are required.", file=sys.stderr)
        return 2   # pipeline.EXIT_NO_REPORT, written out: that module is the missing one

    # Safe at this point: importing the pipeline already brought analyze in. Only BANDS
    # is needed here, for the table of the text report.
    from aisinestes import analyze

    if args.compare:
        return run_compare(args)

    audio_path = os.path.abspath(args.audio[0])
    if not os.path.isfile(audio_path):
        print("ERROR: the audio file does not exist: %s" % audio_path, file=sys.stderr)
        return pipeline.EXIT_NO_REPORT

    # Measurement: everything that produces numbers lives in pipeline.analyze_file. A
    # reading/parsing failure means there is no report to give, which is exit code 2.
    try:
        data = pipeline.analyze_file(audio_path, args.genre)
    except Exception as error:
        print("ERROR reading the WAV: %s" % error, file=sys.stderr)
        return pipeline.EXIT_NO_REPORT

    # --- Outputs ------------------------------------------------------------------
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    out_folder = os.path.join(os.path.abspath(args.out), base_name)
    try:
        os.makedirs(out_folder, exist_ok=True)
    except OSError as error:
        print("ERROR: the output folder could not be created (%s): %s"
              % (out_folder, error), file=sys.stderr)
        return pipeline.EXIT_NO_REPORT
    json_path = os.path.join(out_folder, "report.json")
    txt_path = os.path.join(out_folder, "report.txt")
    html_path = os.path.join(out_folder, "report.html")
    spec_path = os.path.join(out_folder, "spectrogram.png")
    wave_path = os.path.join(out_folder, "waveform.png")

    # --- ffmpeg: images -----------------------------------------------------------
    # Drawing writes files, so it stays here and not in the pipeline. An image is a
    # DERIVED artifact, exactly like report.html: its failure is recorded in `errors`
    # (visible in report.json/report.txt) but it is NOT a measurement, so it does not
    # go into `unmeasured` and cannot hold the verdict back. Without ffmpeg, a clean,
    # fully-checked fx file still exits 0 — which is what "ffmpeg is optional" promises.
    # The only MEASUREMENT ffmpeg provides is loudness, and that one does gate (pipeline).
    # ffmpeg can also exit 0 without writing the file (seen with showwavespic on a
    # 1-sample WAV), so the drawing is only believed if the PNG actually exists.
    images = data["images"]
    for name, target_path, draw in (
        ("spectrogram", spec_path, ffreport.spectrogram),
        ("waveform", wave_path, ffreport.waveform),
    ):
        try:
            draw(audio_path, target_path)
            if not os.path.isfile(target_path):
                raise RuntimeError("ffmpeg reported success but wrote no file")
            images[name] = target_path
        except Exception as error:
            data["errors"].append("%s: %s" % (name, error))

    data["verdict"] = pipeline.build_verdict(data)
    checks = data["checks"]

    # The verdict is already decided above, so nothing that happens from here on can
    # move the exit code. The page goes first only so that a failure while building it
    # still makes it into report.json and report.txt.
    write_report_html(data, checks, analyze.BANDS, html_path)

    text = build_report_txt(data, checks, analyze.BANDS)

    try:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as error:
        print("ERROR writing the report to %s: %s" % (out_folder, error), file=sys.stderr)
        return pipeline.EXIT_NO_REPORT

    if args.brief:
        print(build_brief(data, out_folder))
    else:
        print(text)
        print("Report written to: %s" % out_folder)

    return data["verdict"]["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
