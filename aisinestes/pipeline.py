"""Aisinestes measurement pipeline: everything that turns a WAV into measured facts.

This module is deliberately silent. It reads, measures, judges and returns a dictionary:
it prints nothing and writes nothing to disk. That way the exact same numbers can be
produced by the CLI, by a comparison between two files or by a test, with no output
formatting sitting in the middle.

Two different criteria for errors, and the difference is the whole point:

  - If the audio cannot be read or parsed there is nothing to report, so the exception
    PROPAGATES and the caller decides (the CLI turns it into exit code 2).
  - If an OPTIONAL measurement fails (no ffmpeg -> no EBU R128 loudness), the reason is
    recorded in `errors` and in `unmeasured`, and the rest of the report carries on. That
    is what separates "measured and clean" from "clean as far as it got" — the difference
    between exit code 0 and exit code 3.
"""

import os
import re

from aisinestes import analyze, ffreport, targets, wavio

# Exit codes of the CLI, kept here because the verdict is what decides them and any other
# entry point (comparison, tests) has to use the same numbers.
EXIT_OK = 0            # everything measured, not a single FLAG
EXIT_FLAG = 1          # at least one FLAG: the audio was judged and it failed
EXIT_NO_REPORT = 2     # no report could be produced at all
EXIT_INCOMPLETE = 3    # zero FLAG, but at least one metric was never measured


def analyze_file(audio_path, genre):
    """Measures one WAV file and evaluates it against the profile of `genre`.

    Returns the `data` dictionary the reports are built from. `data["file"]` is the
    ABSOLUTE path (the local reports keep it; whoever publishes something shows only the
    basename). `data["images"]` comes back empty on purpose: drawing images writes files,
    which is the caller's job, and the caller fills that key in.

    Reading/parsing errors are not swallowed: they propagate.
    """
    audio_path = os.path.abspath(audio_path)
    wav = wavio.read(audio_path)
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
    if env and wav["peak"] > 0.0:
        peak_index = max(range(len(env)), key=lambda i: env[i])
        attack_pos = peak_index / max(1, len(env) - 1)
        attack_s = attack_pos * wav["duration"]

    # Digital silence: a spectrum whose total magnitude is zero has no band SHARES —
    # 0/0 is not 0 %. Reporting "sub 0.00 % OK" on an empty file would be inventing a
    # number, so every band goes out as no-data (the checks then flag as unmeasurable,
    # which for a gate is the honest reading of a silent asset). Same for the attack:
    # an envelope with no peak has no peak position (handled above via peak > 0).
    band_total = sum(v for v in bands.values() if isinstance(v, (int, float)))
    if bands and band_total <= 0.0:
        bands = dict.fromkeys(bands, None)

    # --- ffmpeg: loudness ---------------------------------------------------------
    # If ffmpeg fails the reason is recorded and we carry on: the rest of the report is
    # still worth it. But the run will NOT come out as 0, because something went unmeasured.
    errors = []
    unmeasured = []
    loudness = None
    loudness_error = None
    try:
        loudness = ffreport.loudness(audio_path)
    except Exception as error:
        loudness_error = str(error)
        entry = "loudness (ebur128): %s" % loudness_error
        errors.append(entry)
        unmeasured.append(entry)

    data = {
        "file": audio_path,
        "genre": genre,
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
        "images": {},
        "errors": errors,
        # Metrics that were NOT measured, each with its reason. Deliberately a separate
        # list from `errors`: not every error means a metric went missing (writing an
        # optional artifact can fail without leaving any measurement unmeasured), and it
        # is only the missing MEASUREMENTS that may hold the verdict back.
        "unmeasured": unmeasured,
    }
    if loudness_error:
        data["loudness_error"] = loudness_error

    # Version of the profile the file is judged against, so a report can be told apart
    # from another one produced after a threshold moved. Genres with no checks (none)
    # have no version: they judge nothing.
    data["profile_version"] = (targets.GENRES.get(genre) or {}).get("version")

    data["checks"] = targets.evaluate(data, genre)
    return data


def build_verdict(data):
    """Turns the measured facts into the gate result.

    Returns {"flags", "checks", "unmeasured", "exit_code"}.

    A FLAG outranks an unmeasured metric: if the audio was judged and it failed, that is
    the news, and code 1 says so even when something else could not be measured. Code 3
    is reserved for the case where there is nothing to complain about *among what was
    measured*, which is not the same thing as being clean.
    """
    checks = data.get("checks") or []
    flags = sum(1 for item in checks if item.get("status") == "FLAG")
    unmeasured = list(data.get("unmeasured") or [])
    if flags:
        code = EXIT_FLAG
    elif unmeasured:
        code = EXIT_INCOMPLETE
    else:
        code = EXIT_OK
    return {
        "flags": flags,
        "checks": len(checks),
        "unmeasured": unmeasured,
        "exit_code": code,
    }


# Absolute paths (Windows drive, UNC, POSIX) reduced to their last component. Any output
# meant to be pasted elsewhere (the brief, anything shareable) runs through this: local
# reports may keep full paths, but nothing that travels should carry the machine's layout.
_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\s'\"]*[\\/]([^\s'\"\\/]+)")


def scrub_paths(text):
    """Replaces every absolute path inside `text` with its basename."""
    return _PATH_RE.sub(r"\1", str(text))


def unmeasured_labels(unmeasured):
    """Short names of the `unmeasured` entries, for one-line summaries.

    "loudness (ebur128): ffmpeg was not found..." -> "loudness". The reason stays in the
    full entry; here only the name of what is missing is wanted.
    """
    labels = []
    for entry in unmeasured or []:
        label = str(entry).split(":", 1)[0].split(" (", 1)[0].strip()
        if label and label not in labels:
            labels.append(label)
    return labels
