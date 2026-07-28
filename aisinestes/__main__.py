"""Aisinestes — CLI: measures a WAV and writes a readable report + images.

Usage:
    python -m aisinestes <audio.wav> [--genre techno-club|fx-impact|none] [--out out]

Generates, inside <out>/<file name without extension>/:
    report.json       full raw metrics
    report.txt        human-readable report
    spectrogram.png   spectrogram (logarithmic frequency axis, with legend)
    waveform.png      full waveform

Exit codes (meant for using it as a gate):
    0  everything measured and not a single FLAG
    1  there is at least one FLAG, or some metric could not be measured
    2  the report could not be produced (missing file, unsupported WAV, missing module)
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

WIDTH = 74


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
    total = sum(v for v in measured_bands.values() if isinstance(v, (int, float)))
    lines.append("")
    lines.append("  band sum: %s (sanity check: it has to come out ~100 %%)" % fmt_num(total, "%"))
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


def build_report_txt(data, checks, bands_def):
    """Builds the human-readable report from the already computed metrics."""
    signal = data["signal"]
    parts = []
    parts.append("Aisinestes — report")
    parts.append("=" * WIDTH)
    parts.append("File:  %s" % data["file"])
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
        if flag_count:
            parts.append("  RESULT: %d FLAG of %d checks." % (flag_count, len(checks)))
        else:
            parts.append("  RESULT: %d checks, none flagged." % len(checks))
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


def main(argv=None):
    _force_utf8_console()
    parser = argparse.ArgumentParser(
        prog="python -m aisinestes",
        description="Measures a WAV file and writes metrics, per-genre checks and images.",
    )
    parser.add_argument("audio", help="path to the WAV file to analyse")
    parser.add_argument(
        "--genre", default="none", choices=sorted(targets.GENRES),
        help="genre to evaluate against (default: none, metrics only)",
    )
    parser.add_argument(
        "--out", default="out",
        help="base output folder (default: out)",
    )
    args = parser.parse_args(argv)

    # The reading and analysis modules are imported here so we can give a clear message
    # if they are not there yet, instead of an import traceback at startup.
    try:
        from aisinestes import analyze, wavio
    except ImportError as error:
        print("ERROR: modules of the aisinestes package are missing (%s)." % error, file=sys.stderr)
        print("aisinestes/wavio.py and aisinestes/analyze.py are required.", file=sys.stderr)
        return 2

    audio_path = os.path.abspath(args.audio)
    if not os.path.isfile(audio_path):
        print("ERROR: the audio file does not exist: %s" % audio_path, file=sys.stderr)
        return 2

    try:
        wav = wavio.read(audio_path)
    except Exception as error:
        print("ERROR reading the WAV: %s" % error, file=sys.stderr)
        return 2

    samples = wav["samples"]
    rate = wav["rate"]

    # --- Analysis in Python (analyze module) --------------------------------------
    bands = analyze.fft_bands(samples, rate)
    env = analyze.envelope(samples, rate)
    onsets = analyze.onsets(samples, rate)
    stats = analyze.basic_stats(samples, rate)

    # Relative position of the envelope peak (0 = start, 1 = end).
    # It is the metric used by the fx-impact "fast attack" check.
    attack_pos = None
    attack_s = None
    if env:
        peak_index = max(range(len(env)), key=lambda i: env[i])
        attack_pos = peak_index / max(1, len(env) - 1)
        attack_s = attack_pos * wav["duration"]

    # --- Outputs ------------------------------------------------------------------
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    out_folder = os.path.join(os.path.abspath(args.out), base_name)
    os.makedirs(out_folder, exist_ok=True)
    json_path = os.path.join(out_folder, "report.json")
    txt_path = os.path.join(out_folder, "report.txt")
    spec_path = os.path.join(out_folder, "spectrogram.png")
    wave_path = os.path.join(out_folder, "waveform.png")

    errors = []

    # --- ffmpeg: loudness and images ----------------------------------------------
    # If ffmpeg fails, the error is recorded and we carry on: the rest of the report is
    # still worth it. But the process will NOT return 0, because something went unmeasured.
    loudness = None
    loudness_error = None
    try:
        loudness = ffreport.loudness(audio_path)
    except Exception as error:
        loudness_error = str(error)
        errors.append("loudness (ebur128): %s" % loudness_error)

    images = {}
    try:
        ffreport.spectrogram(audio_path, spec_path)
        images["spectrogram"] = spec_path
    except Exception as error:
        errors.append("spectrogram: %s" % error)
    try:
        ffreport.waveform(audio_path, wave_path)
        images["waveform"] = wave_path
    except Exception as error:
        errors.append("waveform: %s" % error)

    data = {
        "file": audio_path,
        "genre": args.genre,
        "signal": {
            "rate": rate,
            "channels": wav["channels"],
            "bits": wav["bits"],
            "duration": wav["duration"],
            "peak": wav["peak"],
            "peak_db": stats["peak_db"],
            "rms_db": stats["rms_db"],
            "dc_offset": stats["dc_offset"],
        },
        "loudness": loudness,
        "bands": bands,
        "onsets": onsets,
        "attack_pos": attack_pos,
        "attack_s": attack_s,
        "envelope": {
            "win_ms": 10,
            "n": len(env),
            "values": list(env),
        },
        "images": images,
        "errors": errors,
    }
    if loudness_error:
        data["loudness_error"] = loudness_error

    checks = targets.evaluate(data, args.genre)
    data["checks"] = checks

    text = build_report_txt(data, checks, analyze.BANDS)

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    print(text)
    print("Report written to: %s" % out_folder)

    has_flags = any(c["status"] == "FLAG" for c in checks)
    if has_flags or errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
