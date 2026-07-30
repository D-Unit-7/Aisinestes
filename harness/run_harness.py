# -*- coding: utf-8 -*-
"""
run_harness.py — truth assertions for Aisinestes.

Runs the instrument (aisinestes.*) against the synthetic signals of make_signals.py,
about which we know EVERYTHING beforehand, and checks that it measures the truth.

Most cases call the modules directly. The ones about the exit code (family "exitcode")
and about --brief (family "brief") run the real CLI in a child process instead: the gate
IS the exit code, and no function call can prove what the command actually returns.

House rule: **a test is only worth anything if it can fail**. We have already had a
green harness with the bug still inside. That is why every family of assertions runs in
two modes, through THE SAME function:

  - POSITIVE mode: correct expectation  -> it has to pass.
  - NEGATIVE mode: wrong expectation (or the wrong signal) -> it has to RAISE
    AssertionError. If a negative comes out green, that assertion proves nothing and it
    is reported as a HARNESS DEFECT, not as a success.

Possible statuses per case:
  PASS          the case did what it was supposed to do
  FAIL          the instrument does not measure the truth (or the negative could not fail)
  NOT RUN       missing module / missing ffmpeg / time budget exhausted
  TIMEOUT       the case hung
  INCONCLUSIVE  the negative blew up for another reason before reaching the assertion

Exit code:  0 = all PASS · 1 = there are FAILs · 2 = no FAIL but something did not run.
(A missing module is NEVER reported as PASS.)

Usage:
    python run_harness.py                  # positives + negatives
    python run_harness.py --mode positive
    python run_harness.py --mode negative
    python run_harness.py --case b01       # filters by id prefix
    python run_harness.py --simulate-missing ffreport,targets

That last flag exists so the "NOT RUN" branch can be exercised while the modules ARE
there: it forces the absence and verifies that those cases are reported as not run
(never as PASS) and that the exit code is 2. Simulating an absence cannot turn anything
green.
"""

import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
SIG_DIR = os.path.join(BASE, "signals")
# Output folder for the cases that run the real CLI in a child process. It hangs off out/,
# which is already gitignored, and each run wipes its own subfolder before starting.
CLI_OUT = os.path.join(ROOT, "out", "_harness")

# The project root goes first so the aisinestes package can be imported.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import make_signals as ms   # noqa: E402  (signal truths + contract bands)

# The harness uses the SAME ffmpeg as the tool: that way the cross-check against EBU R128
# compares against the real binary that will be used, and not against another one that could
# be a different version. Without ffmpeg the cases that depend on it are skipped and the
# rest still runs.
try:
    from aisinestes.ffreport import _find_ffmpeg
    FFMPEG = _find_ffmpeg()
except Exception:
    FFMPEG = shutil.which("ffmpeg") or ""

DEFAULT_TIMEOUT = 45.0   # seconds per case
TOTAL_BUDGET = 165.0     # seconds for the whole harness (the ask is < ~3 min)


# ---------------------------------------------------------------------------
# Tolerant import of the modules owned by the other agents
# ---------------------------------------------------------------------------

MODULES = {}
MISSING_REASON = {}


def _import(name):
    """Imports aisinestes.<name> without blowing up if it does not exist yet."""
    try:
        mod = __import__("aisinestes." + name, fromlist=[name])
        MODULES[name] = mod
    except Exception as e:
        MODULES[name] = None
        MISSING_REASON[name] = "%s: %s" % (type(e).__name__, e)


# pipeline, compare and htmlreport are imported the same tolerant way as the rest: if one
# of them is not there its cases have to come out NOT RUN, never PASS.
MODULE_NAMES = ("wavio", "analyze", "ffreport", "targets", "pipeline", "compare",
                "htmlreport")

for _m in MODULE_NAMES:
    _import(_m)


def has_ffmpeg():
    return os.path.exists(FFMPEG)


# ---------------------------------------------------------------------------
# Cache: the same reads and analyses are reused across cases (positive and negative)
# ---------------------------------------------------------------------------

_CACHE = {}


def _cache(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def signal_path(file_name):
    p = os.path.join(SIG_DIR, file_name)
    if not os.path.exists(p):
        raise RuntimeError("missing signal %s (run make_signals.py first)" % file_name)
    return p


def read_signal(file_name):
    return _cache(("read", file_name),
                  lambda: MODULES["wavio"].read(signal_path(file_name)))


def bands(file_name):
    d = read_signal(file_name)
    return _cache(("bands", file_name),
                  lambda: MODULES["analyze"].fft_bands(d["samples"], d["rate"]))


def stats(file_name):
    d = read_signal(file_name)
    return _cache(("stats", file_name),
                  lambda: MODULES["analyze"].basic_stats(d["samples"], d["rate"]))


def onsets(file_name):
    d = read_signal(file_name)
    return _cache(("onsets", file_name),
                  lambda: MODULES["analyze"].onsets(d["samples"], d["rate"]))


def envelope(file_name):
    d = read_signal(file_name)
    return _cache(("env", file_name),
                  lambda: MODULES["analyze"].envelope(d["samples"], d["rate"]))


def loudness(file_name):
    return _cache(("loud", file_name),
                  lambda: MODULES["ffreport"].loudness(signal_path(file_name)))


def _finite(x):
    return isinstance(x, (int, float)) and math.isfinite(float(x))


# ---------------------------------------------------------------------------
# Building the metrics dict for targets.evaluate()
# ---------------------------------------------------------------------------

def metrics(file_name, with_loudness=True):
    """
    The CONTRACT fixes the signature `evaluate(metrics, genre)` but NOT the keys of
    `metrics`. So as not to fail over a naming disagreement, a superset is built with
    aliases (flat and nested for the same value). If a key is still missing, the case
    reports FAIL with the KeyError in plain sight: that is a real ambiguity of the
    contract, not a detail to hide.
    """
    d = read_signal(file_name)
    b = bands(file_name)
    st = stats(file_name)
    on = onsets(file_name)
    env = envelope(file_name)

    # Envelope peak position, normalized to [0,1]: the fx-impact "fast attack" check
    # asks for the peak inside the first 15 %.
    peak_pos = 0.0
    if env:
        i_peak = max(range(len(env)), key=lambda i: env[i])
        peak_pos = i_peak / float(len(env))

    m = {
        "path": signal_path(file_name), "file": file_name,
        "rate": d["rate"], "channels": d["channels"],
        "bits": d["bits"], "duration": d["duration"],
        "peak": d["peak"],
        "bands": b, "fft_bands": b,
        "rms_db": st.get("rms_db"), "peak_db": st.get("peak_db"),
        "dc_offset": st.get("dc_offset"),
        "stats": st, "basic_stats": st,
        "onsets": on, "count": on.get("count"), "bpm": on.get("bpm"),
        "envelope": env,
        "attack_pos": peak_pos,            # the key targets.evaluate() documents
        "envelope_peak_pos": peak_pos,
        "fast_attack": peak_pos <= 0.15,
    }
    if with_loudness and MODULES["ffreport"] is not None and has_ffmpeg():
        try:
            ld = loudness(file_name)
            m.update({"lufs_i": ld.get("lufs_i"), "lra": ld.get("lra"),
                      "true_peak_db": ld.get("true_peak_db"),
                      "loudness": ld})
        except Exception:
            pass   # the loudness case has its own assertion; nothing is covered up here
    return m


# Absolute-path roots that must never show up in an output built to travel. The Windows
# drive letter is caught by the regex below; these are the POSIX ones, and they include
# the system directories ffmpeg quotes inside its own error text.
POSIX_PATH_ROOTS = ("/home/", "/Users/", "/root/", "/tmp/", "/usr/", "/var/", "/opt/")


def absolute_path_hint(text):
    """Returns the token that gives an absolute path away inside `text`, or None.

    The two outputs made to leave the machine — the brief and the HTML page — must not
    carry the path they were measured from. This guard used to look only for a Windows
    drive letter, which meant that on Linux and macOS it **could not fail**: a leaked
    `/home/runner/...` matched nothing and the sweep passed while testing nothing. Since
    the CI matrix certifies exactly those two platforms, the check had to learn their
    shape of an absolute path before that green could mean anything.
    """
    match = re.search(r"[A-Za-z]:[\\/]", text)
    if match:
        return match.group(0)
    for root in POSIX_PATH_ROOTS:
        if root in text:
            return root
    return None


# ---------------------------------------------------------------------------
# The assertion functions. Each one takes ITS expectation as a parameter:
# the negative mode calls exactly the same function with the wrong expectation.
# ---------------------------------------------------------------------------

def chk_contract_bands(expected):
    b = [(n, float(lo), float(hi)) for n, lo, hi in MODULES["analyze"].BANDS]
    assert b == expected, "analyze.BANDS does not match the CONTRACT.\n    measured: %r" % (b,)
    return "6 exact bands as per contract"


def chk_band(file_name, band, min_pct):
    bb = bands(file_name)
    assert band in bb, "fft_bands did not return the band %r (returned %r)" % (band, sorted(bb))
    val = float(bb[band])
    assert val >= min_pct, ("%s: band '%s' = %.3f %% < %.2f %% required "
                            "(measured split: %s)"
                            % (file_name, band, val, min_pct, _fmt_bands(bb)))
    return "%s: %s = %.2f %% (>= %.1f %%)" % (file_name, band, val, min_pct)


def chk_band_sum(file_name, expected, tol):
    bb = bands(file_name)
    s = sum(float(v) for v in bb.values())
    assert abs(s - expected) <= tol, ("%s: the bands add up to %.3f, expected %.1f +-%.1f"
                                      % (file_name, s, expected, tol))
    return "%s: band sum = %.3f" % (file_name, s)


def chk_levels(file_name, peak_db, tol_peak, rms_db, tol_rms):
    st = stats(file_name)
    p, r = float(st["peak_db"]), float(st["rms_db"])
    assert abs(p - peak_db) <= tol_peak, ("%s: peak_db = %.4f, expected %.2f +-%.2f"
                                          % (file_name, p, peak_db, tol_peak))
    assert abs(r - rms_db) <= tol_rms, ("%s: rms_db = %.4f, expected %.2f +-%.2f"
                                        % (file_name, r, rms_db, tol_rms))
    return "%s: peak %.3f dB / rms %.3f dB" % (file_name, p, r)


def chk_white_noise(file_name, tol_rel, tol_abs):
    """
    White noise = flat spectral density -> the energy of each band is proportional to
    its WIDTH in Hz. The expected value is computed right here from the contract bands
    and normalized over the 6 of them (which is what fft_bands returns, adding to ~100).
    Tolerance: ±tol_rel relative or ±tol_abs points, whichever is LARGER (narrow bands
    such as sub are worth 0.25 % and a purely relative tolerance would be absurd there).
    """
    bb = bands(file_name)
    widths = {n: (hi - lo) for n, lo, hi in ms.CONTRACT_BANDS}
    total = sum(widths.values())
    bad = []
    detail = []
    for n, _lo, _hi in ms.CONTRACT_BANDS:
        exp = 100.0 * widths[n] / total
        assert n in bb, "fft_bands did not return the band %r" % n
        got = float(bb[n])
        tol = max(exp * tol_rel, tol_abs)
        detail.append("%s %.2f/%.2f" % (n, got, exp))
        if abs(got - exp) > tol:
            bad.append("%s: measured %.3f %% vs expected %.3f %% (tol +-%.3f)"
                       % (n, got, exp, tol))
    assert not bad, "%s: bands out of tolerance -> %s" % (file_name, "; ".join(bad))
    return "measured/expected -> " + ", ".join(detail)


def chk_silence(file_name, peak_max, peak_db_max, onsets_max):
    """
    'The report must NOT say there is audio.' Operationalized like this: peak exactly 0,
    peak_db and rms_db down at the floor, zero onsets, and no NaN in the bands (a badly
    handled 0/0 turns silence into 'energy').
    The negative of this family feeds it a file WITH audio: if the assertion does not
    complain, it is not looking at anything.
    """
    d = read_signal(file_name)
    st = stats(file_name)
    on = onsets(file_name)
    bb = bands(file_name)

    assert float(d["peak"]) <= peak_max, ("%s: linear peak = %.6f, required <= %.6f"
                                          % (file_name, float(d["peak"]), peak_max))
    for k in ("peak_db", "rms_db"):
        v = float(st[k])
        assert v <= peak_db_max, ("%s: %s = %.3f dB, required <= %.1f dB "
                                  "(in other words: the report is saying there is audio)"
                                  % (file_name, k, v, peak_db_max))
    c = int(on["count"])
    assert c <= onsets_max, "%s: onsets count = %d, required <= %d" % (file_name, c, onsets_max)
    bad = [k for k, v in bb.items() if not _finite(v)]
    assert not bad, "%s: fft_bands returned non-finite values in %r" % (file_name, bad)
    return "%s: peak %.6f / peak_db %s / rms_db %s / onsets %d" % (
        file_name, float(d["peak"]), st["peak_db"], st["rms_db"], c)


def chk_bpm(file_name, lo, hi):
    on = onsets(file_name)
    bpm = on.get("bpm")
    assert bpm is not None, "%s: onsets() returned bpm=None" % file_name
    bpm = float(bpm)
    assert lo <= bpm <= hi, ("%s: bpm = %.3f outside [%.1f, %.1f] "
                             "(watch out: 122 was the value of the old detector that "
                             "swallowed transients; onsets detected: %s)"
                             % (file_name, bpm, lo, hi, on.get("count")))
    return "%s: bpm = %.3f in [%.1f, %.1f], onsets = %s" % (file_name, bpm, lo, hi,
                                                            on.get("count"))


def chk_onsets_count(file_name, expected):
    on = onsets(file_name)
    c = int(on["count"])
    assert c == expected, ("%s: onsets count = %d, expected %d (times = %s)"
                           % (file_name, c, expected, on.get("times")))
    return "%s: count = %d, times = %s" % (file_name, c, _fmt_times(on.get("times")))


def chk_onset_spacing(file_name, expected_sep_ms, tol_ms):
    """
    Counting 2 onsets is not enough: it has to be checked that they are WHERE they are.
    The truth of two_clicks_100ms.wav is a spacing of exactly 100.000 ms.
    """
    on = onsets(file_name)
    ts = list(on.get("times") or [])
    assert len(ts) >= 2, "%s: 2 onsets are needed to measure spacing, there are %d" % (
        file_name, len(ts))
    sep = (ts[1] - ts[0]) * 1000.0
    assert abs(sep - expected_sep_ms) <= tol_ms, (
        "%s: measured spacing %.3f ms, expected %.1f +-%.1f ms (times = %s)"
        % (file_name, sep, expected_sep_ms, tol_ms, _fmt_times(ts)))
    return "%s: spacing = %.3f ms (expected %.1f +-%.1f)" % (
        file_name, sep, expected_sep_ms, tol_ms)


def chk_equivalence(file_a, file_b, tol_db, tol_band_pct):
    """
    The float32 WAV has to give the SAME numbers as its PCM16 twin (they were generated
    from the same integers). It compares level AND the per-band split: without the bands,
    two different sines of equal amplitude would pass just the same and the assertion
    would prove nothing.
    """
    sa, sb = stats(file_a), stats(file_b)
    ba, bb = bands(file_a), bands(file_b)
    for k in ("peak_db", "rms_db"):
        diff = abs(float(sa[k]) - float(sb[k]))
        assert diff <= tol_db, ("%s vs %s: %s differs by %.4f dB (> %.2f): %.4f vs %.4f"
                                % (file_a, file_b, k, diff, tol_db,
                                   float(sa[k]), float(sb[k])))
    for n, _lo, _hi in ms.CONTRACT_BANDS:
        diff = abs(float(ba.get(n, 0.0)) - float(bb.get(n, 0.0)))
        assert diff <= tol_band_pct, ("%s vs %s: band '%s' differs by %.3f points (> %.2f)"
                                      % (file_a, file_b, n, diff, tol_band_pct))
    return "%s == %s (peak %.3f/%.3f dB)" % (file_a, file_b,
                                             float(sa["peak_db"]), float(sb["peak_db"]))


def chk_loudness(file_name, lufs_expected, tol, tol_vs_rms=None, tp_expected=None, tol_tp=0.5):
    """
    Cross-check between TWO independent instruments: our own FFT/RMS and ffmpeg's
    ebur128. If the two agree it is very unlikely that they are wrong in the same way.

    CAREFUL with the expected value: LUFS is an RMS-type measurement (quadratic mean),
    not a peak one. The K-weighting at 1 kHz is indeed ~0 dB, and precisely because of
    that LUFS-I has to come out equal to the RMS, not equal to the peak: a sine whose
    PEAK sits at -20 dBFS has its RMS at -20 - 3.01 = -23.01 dBFS, i.e. LUFS-I ~ -23.
    The one that is compared against the peak is the true peak, which does have to
    come out at -20.
    Verified by hand with ffmpeg -filter_complex ebur128=peak=true: I = -23.0 LUFS,
    True peak = -20.0 dBFS.
    """
    ld = loudness(file_name)
    v = ld.get("lufs_i")
    assert v is not None and _finite(v), "%s: invalid lufs_i -> %r" % (file_name, v)
    v = float(v)
    rms = float(stats(file_name)["rms_db"])
    assert abs(v - lufs_expected) <= tol, ("%s: LUFS-I = %.3f, expected %.1f +-%.1f "
                                           "(our own rms_db: %.4f)"
                                           % (file_name, v, lufs_expected, tol, rms))
    if tol_vs_rms is not None:
        # The cross-check proper: two instruments, same number.
        assert abs(v - rms) <= tol_vs_rms, (
            "%s: ffmpeg says LUFS-I %.3f and our FFT says rms_db %.3f -> they differ by "
            "%.3f dB (> %.2f)" % (file_name, v, rms, abs(v - rms), tol_vs_rms))
    if tp_expected is not None:
        tp = ld.get("true_peak_db")
        assert tp is not None and _finite(tp), ("%s: invalid true_peak_db -> %r"
                                                % (file_name, tp))
        assert abs(float(tp) - tp_expected) <= tol_tp, (
            "%s: true peak = %.3f dBFS, expected %.1f +-%.2f"
            % (file_name, float(tp), tp_expected, tol_tp))
    return "%s: LUFS-I %.3f vs our own rms %.3f dB (diff %.3f), true peak %s" % (
        file_name, v, rms, abs(v - rms), ld.get("true_peak_db"))


def chk_fx_flags(file_name, genre, required_flags=(), no_flags=False):
    """
    Evaluates against targets.evaluate and looks at the statuses. `required_flags` are
    substrings that have to show up in the 'check' field of some item in FLAG status.
    The match is case-insensitive, but the substrings still have to belong to the label
    that targets.py actually emits ("Sub magnitude...", "Bite magnitude...").
    """
    items = MODULES["targets"].evaluate(metrics(file_name), genre)
    assert isinstance(items, list), "targets.evaluate did not return a list: %r" % type(items)
    for it in items:
        for k in ("check", "measured", "target", "status"):
            assert k in it, "item from evaluate without the key %r: %r" % (k, it)
        assert it["status"] in ("OK", "FLAG"), "invalid status %r" % it["status"]

    flags = [it for it in items if it["status"] == "FLAG"]
    summary = " | ".join("%s=%s[%s]" % (it["check"], it["measured"], it["status"])
                         for it in items)
    if no_flags:
        assert not flags, ("%s (%s): 0 FLAG were expected and there are %d -> %s"
                           % (file_name, genre, len(flags),
                              "; ".join("%s (measured %s)" % (f["check"], f["measured"])
                                        for f in flags)))
    for req in required_flags:
        found = any(req.lower() in str(f["check"]).lower() for f in flags)
        assert found, ("%s (%s): the FLAG mentioning %r is missing. Checks: %s"
                       % (file_name, genre, req, summary))
    return "%s (%s): %d checks, %d FLAG -> %s" % (file_name, genre, len(items),
                                                  len(flags), summary)


# ---------------------------------------------------------------------------
# CLI cases: the gate is the exit code, so the exit code gets tested by running
# the real command in a child process, not by calling functions.
# ---------------------------------------------------------------------------

CLI_TIMEOUT = 60.0      # per child process; the case timeout has to be LARGER than this
                        # or the harness thread would give up while the child keeps running.


def run_cli(file_name, genre, brief=False, break_ffmpeg=False):
    """Runs `python -m aisinestes` on a signal and returns what came out of it.

    Returns {"code", "stdout", "stderr", "folder", "report"}.

    break_ffmpeg=True points AISINESTES_FFMPEG at the WAV itself: a file that EXISTS (so
    _find_ffmpeg stops there and does not fall back to ffmpeg.local or to the PATH) but
    that cannot be executed. That is the only reliable way to reproduce "no ffmpeg" on a
    machine that does have it — pointing the variable at a path that does not exist would
    simply be ignored and the real binary would be found anyway.
    """
    key = ("cli", file_name, genre, brief, break_ffmpeg)

    def _run():
        out_dir = os.path.join(CLI_OUT, "%s-%s%s%s" % (
            os.path.splitext(file_name)[0], genre,
            "-brief" if brief else "", "-noffmpeg" if break_ffmpeg else ""))
        # Wiped first: otherwise a leftover report from an earlier run could pass for one
        # this run never wrote.
        shutil.rmtree(out_dir, ignore_errors=True)
        env = dict(os.environ)
        if break_ffmpeg:
            env["AISINESTES_FFMPEG"] = signal_path(file_name)
        cmd = [sys.executable, "-m", "aisinestes", signal_path(file_name),
               "--genre", genre, "--out", out_dir]
        if brief:
            cmd.append("--brief")
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=CLI_TIMEOUT)
        folder = os.path.join(out_dir, os.path.splitext(file_name)[0])
        report = None
        json_path = os.path.join(folder, "report.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
        return {
            "code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace"),
            "stderr": proc.stderr.decode("utf-8", errors="replace"),
            "folder": folder,
            "report": report,
        }

    return _cache(key, _run)


def chk_cli_exit(file_name, genre, expect_code, break_ffmpeg=False,
                 expect_flags=None, expect_unmeasured=None):
    """
    Checks the exit code of the real CLI and that the verdict block in report.json says
    the same thing. The two have to agree: an exit code nobody can explain from the report
    is a gate nobody can trust.
    expect_unmeasured: True = the list has to have something, False = it has to be empty.
    """
    got = run_cli(file_name, genre, break_ffmpeg=break_ffmpeg)
    report = got["report"]
    assert report is not None, ("%s (%s): no report.json was written in %s. stderr: %s"
                                % (file_name, genre, got["folder"],
                                   _one_line(got["stderr"], 200)))
    verdict = report.get("verdict")
    assert verdict is not None, ("%s: report.json has no 'verdict' block (keys: %s)"
                                 % (file_name, sorted(report)))
    assert got["code"] == expect_code, (
        "%s (%s%s): the CLI returned %d and %d was expected. verdict=%s"
        % (file_name, genre, ", ffmpeg broken" if break_ffmpeg else "",
           got["code"], expect_code, _verdict_summary(verdict)))
    assert verdict["exit_code"] == got["code"], (
        "%s: verdict.exit_code = %r but the process returned %d"
        % (file_name, verdict["exit_code"], got["code"]))
    if expect_flags is not None:
        assert verdict["flags"] == expect_flags, (
            "%s (%s): verdict.flags = %d, expected %d"
            % (file_name, genre, verdict["flags"], expect_flags))
    if expect_unmeasured is True:
        assert verdict["unmeasured"], (
            "%s (%s): verdict.unmeasured is empty and something had to be missing there"
            % (file_name, genre))
    if expect_unmeasured is False:
        assert not verdict["unmeasured"], (
            "%s (%s): verdict.unmeasured is NOT empty -> %s"
            % (file_name, genre, _one_line("; ".join(verdict["unmeasured"]), 200)))
    return "%s (%s%s): exit %d, %s" % (
        file_name, genre, ", ffmpeg broken" if break_ffmpeg else "",
        got["code"], _verdict_summary(verdict))


def chk_brief(file_name, genre, expect_verdict, expect_flag_lines, expect_code=None,
              break_ffmpeg=False):
    """
    Parses the --brief output exactly as a machine reading it would: line by line.
    Also checks that --brief did NOT stop the full report from being written, and that
    the literal `exit=` line matches the code the process actually returned.
    """
    got = run_cli(file_name, genre, brief=True, break_ffmpeg=break_ffmpeg)
    lines = [ln for ln in got["stdout"].splitlines() if ln.strip()]
    assert lines, "%s: --brief printed nothing. stderr: %s" % (
        file_name, _one_line(got["stderr"], 200))
    assert len(lines) <= 20, "%s: the brief came out with %d lines, the cap is 20" % (
        file_name, len(lines))

    head = lines[0]
    assert head.startswith("AISINESTES %s |" % file_name), (
        "%s: the first line does not start with 'AISINESTES <basename> |' -> %r"
        % (file_name, head))
    first_field = head.split("|")[0]
    assert "/" not in first_field and "\\" not in first_field, (
        "%s: the brief header is leaking a path, it has to show only the basename -> %r"
        % (file_name, head))
    assert "genre=%s" % genre in head, "%s: 'genre=%s' is missing in %r" % (
        file_name, genre, head)
    version = (MODULES["targets"].GENRES.get(genre) or {}).get("version")
    if version:
        assert "genre=%s v%s" % (genre, version) in head, (
            "%s: the profile version is missing in the header -> %r" % (file_name, head))

    # No line of the brief may carry an absolute path — not the header, not the
    # UNMEASURED reasons (ffmpeg's own words include full paths), not `files:`. The
    # brief is the output made to be pasted elsewhere.
    for line in lines:
        hint = absolute_path_hint(line)
        assert hint is None, (
            "%s: the brief is leaking an absolute path (%r) -> %r"
            % (file_name, hint, line))

    assert lines[1].startswith("VERDICT: %s" % expect_verdict), (
        "%s: expected 'VERDICT: %s' and got %r" % (file_name, expect_verdict, lines[1]))
    flag_lines = [ln for ln in lines if ln.startswith("FLAG ")]
    assert len(flag_lines) == expect_flag_lines, (
        "%s: %d FLAG lines expected in the brief, there are %d -> %s"
        % (file_name, expect_flag_lines, len(flag_lines),
           _one_line(" / ".join(flag_lines), 300)))
    assert any(ln.startswith("files: ") for ln in lines), (
        "%s: the brief has no 'files:' line -> %r" % (file_name, lines))
    assert lines[-1] == "exit=%d" % got["code"], (
        "%s: the last brief line is %r and the process returned %d — a brief that lies "
        "about its own exit code is worse than no brief"
        % (file_name, lines[-1], got["code"]))
    if expect_code is not None:
        assert got["code"] == expect_code, (
            "%s: the CLI returned %d and %d was expected" % (
                file_name, got["code"], expect_code))
    for name in ("report.json", "report.txt"):
        assert os.path.exists(os.path.join(got["folder"], name)), (
            "%s: --brief did not write %s (it has to write the same files as always)"
            % (file_name, name))
    return "%s (%s): %s | %d FLAG lines | %d lines | exit %d" % (
        file_name, genre, lines[1], len(flag_lines), len(lines), got["code"])


def chk_missing_file(expect_code):
    """The classic error route: a file that does not exist has to come out as exit 2,
    with a clear message on stderr — not a traceback, not a half-written report."""
    ghost = os.path.join(CLI_OUT, "no-such-file.wav")
    out_dir = os.path.join(CLI_OUT, "missing-file")
    shutil.rmtree(out_dir, ignore_errors=True)
    proc = subprocess.run([sys.executable, "-m", "aisinestes", ghost,
                           "--genre", "fx-impact", "--out", out_dir],
                          cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=CLI_TIMEOUT)
    stderr = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == expect_code, (
        "missing file: the CLI returned %d and %d was expected. stderr: %s"
        % (proc.returncode, expect_code, _one_line(stderr, 200)))
    assert "ERROR" in stderr, (
        "missing file: stderr carries no ERROR message -> %r" % _one_line(stderr, 200))
    assert "Traceback" not in stderr, "missing file: a traceback leaked to stderr"
    assert not os.path.exists(os.path.join(out_dir, "no-such-file", "report.json")), (
        "missing file: a report.json was written for a file that does not exist")
    return "missing file: exit %d, clean ERROR message, no report written" % proc.returncode


# ---------------------------------------------------------------------------
# Comparison between two files, and the HTML page. Both are checked on the REAL
# artifacts written by the CLI: an HTML that is self-contained "by construction" is
# only self-contained if the file on disk says so.
# ---------------------------------------------------------------------------

def _stem(file_name):
    return os.path.splitext(file_name)[0]


def analyzed(file_name, genre):
    """pipeline.analyze_file on a signal, cached. Returns the data dict.

    Whoever is going to MODIFY it makes a copy first: the object is shared between cases.
    """
    return _cache(("analyzed", file_name, genre),
                  lambda: MODULES["pipeline"].analyze_file(signal_path(file_name), genre))


def run_compare_cli(old_file, new_file, genre, brief=False, break_ffmpeg=False):
    """Runs `python -m aisinestes old new --compare` and returns what came out.

    Returns {"code", "stdout", "stderr", "folder", "report"} with `report` = compare.json.
    break_ffmpeg works the same way as in run_cli: the variable points at a file that
    exists and cannot be executed, which is the only reliable way to reproduce "no
    ffmpeg" on a machine that has it.
    """
    key = ("cmpcli", old_file, new_file, genre, brief, break_ffmpeg)

    def _run():
        out_dir = os.path.join(CLI_OUT, "compare-%s-%s-%s%s%s" % (
            _stem(old_file), _stem(new_file), genre, "-brief" if brief else "",
            "-noffmpeg" if break_ffmpeg else ""))
        shutil.rmtree(out_dir, ignore_errors=True)
        env = dict(os.environ)
        if break_ffmpeg:
            env["AISINESTES_FFMPEG"] = signal_path(new_file)
        cmd = [sys.executable, "-m", "aisinestes",
               signal_path(old_file), signal_path(new_file),
               "--compare", "--genre", genre, "--out", out_dir]
        if brief:
            cmd.append("--brief")
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=CLI_TIMEOUT)
        folder = os.path.join(out_dir, "compare_%s_vs_%s" % (_stem(old_file),
                                                             _stem(new_file)))
        report = None
        json_path = os.path.join(folder, "compare.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
        return {
            "code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace"),
            "stderr": proc.stderr.decode("utf-8", errors="replace"),
            "folder": folder,
            "report": report,
        }

    return _cache(key, _run)


def _find_metric(cmp, needle):
    hits = [m for m in cmp["metrics"] if needle.lower() in str(m.get("name")).lower()]
    assert hits, "no metric mentioning %r. There are: %s" % (
        needle, ", ".join(str(m.get("name")) for m in cmp["metrics"]))
    return hits[0]


def _cmp_summary(cmp):
    return " | ".join("%s %s->%s %s/%s" % (m["name"], m["old"], m["new"],
                                           m["direction"], m["transition"])
                      for m in cmp["metrics"])


def chk_compare(old_file, new_file, genre, expect, require_mixed=False):
    """
    Compares two signals and checks the DIRECTION of each metric.

    `expect` maps a piece of the metric name to the (direction, transition) pair that
    metric has to come out with. The negative mode passes the directions the other way
    round: if the comparison cannot tell an improvement from a regression, that
    expectation comes out green and the case is worthless.

    `require_mixed` demands that the same comparison contain at least one improvement AND
    at least one regression. That is the situation the feature was built for — a round of
    fixes that quietly costs a metric nobody was watching — and it needs to be asserted,
    not assumed.
    """
    cmp = MODULES["compare"].compare(analyzed(old_file, genre), analyzed(new_file, genre))
    assert cmp.get("metrics"), "compare() returned no metrics"
    assert cmp["genre"] == genre, "compare() reports genre %r" % cmp["genre"]
    for side, name in (("old", old_file), ("new", new_file)):
        assert cmp[side]["file"] == name, (
            "compare()['%s']['file'] = %r and the basename is %r (never the full path)"
            % (side, cmp[side]["file"], name))

    # Two invariants that hold for every metric of every comparison, checked here so no
    # future profile can slip past them.
    for m in cmp["metrics"]:
        # A missing side is never a zero.
        if m["old"] is None or m["new"] is None:
            assert m["direction"] == "not_comparable", (
                "%s: a side is missing and direction says %r" % (m["name"], m["direction"]))
            assert m["delta"] is None, (
                "%s: a side was never measured and a delta of %r came out — a made-up "
                "number is worse than no number" % (m["name"], m["delta"]))
        # A metric the profile has no reference for cannot be better or worse, however
        # small the move: "unchanged" there is a judgement nobody is in a position to make.
        if m.get("target") in ("-", None):
            assert m["direction"] == "not_comparable", (
                "%s: target is '-' (no reference in this profile) and direction says %r "
                "-- with nothing to compare against, the only honest answer is "
                "not_comparable" % (m["name"], m["direction"]))

    for needle, (direction, transition) in expect.items():
        m = _find_metric(cmp, needle)
        assert m["direction"] == direction, (
            "%s: direction %r, expected %r (old=%s new=%s delta=%s, target %s)"
            % (m["name"], m["direction"], direction, m["old"], m["new"], m["delta"],
               m["target"]))
        assert m["transition"] == transition, (
            "%s: transition %r, expected %r" % (m["name"], m["transition"], transition))

    if require_mixed:
        directions = {m["direction"] for m in cmp["metrics"]}
        assert "improved" in directions and "worsened" in directions, (
            "this comparison had to contain an improvement AND a regression at the same "
            "time, and it contains %s" % sorted(directions))

    return "%s -> %s (%s): %s" % (old_file, new_file, genre, _cmp_summary(cmp))


def chk_compare_crossing(metric_name, target, old, new, status_old, status_new,
                         expect_direction, expect_transition):
    """
    A check that CROSSES its threshold with a tiny step.

    This is the one the epsilon got wrong: 0.05 absolute is wider than the whole decision
    window of the small metrics (the attack lives between 0 and 0.15, the bite floor sits
    at 0.16 %), so a move that flipped the verdict came out as "unchanged" sitting right
    next to a transition of "broke". A direction that contradicts the gate is worse than
    no direction at all, which is why the crossing now outranks the epsilon.

    The two sides are built by hand, exactly in the shape targets.evaluate emits, because
    what is under test is the arithmetic of the comparison and not the synthesis of a WAV
    that would land on a given decimal.
    """
    item_old = {"check": metric_name, "measured": "%.3f" % old, "target": target,
                "status": status_old}
    item_new = {"check": metric_name, "measured": "%.3f" % new, "target": target,
                "status": status_new}
    data_old = {"genre": "fx-impact", "file": "before.wav", "checks": [item_old]}
    data_new = {"genre": "fx-impact", "file": "after.wav", "checks": [item_new]}
    cmp = MODULES["compare"].compare(data_old, data_new)
    metric = _find_metric(cmp, metric_name)

    assert metric["old"] == old and metric["new"] == new, (
        "%s: the values did not survive the round trip -> old=%r new=%r"
        % (metric_name, metric["old"], metric["new"]))
    step = abs(new - old)
    assert step < 0.05, (
        "%s: the step is %.4f and this case only means something below the old absolute "
        "epsilon of 0.05" % (metric_name, step))
    assert metric["transition"] == expect_transition, (
        "%s: transition %r, expected %r" % (metric_name, metric["transition"],
                                            expect_transition))
    assert metric["direction"] == expect_direction, (
        "%s: %.3f -> %.3f (step %.4f) crossed the threshold %r and direction says %r, "
        "expected %r — a direction that contradicts the transition is the bug"
        % (metric_name, old, new, step, target, metric["direction"], expect_direction))
    return "%s: %.3f -> %.3f (step %.4f) -> %s / %s" % (
        metric_name, old, new, step, metric["direction"], metric["transition"])


def chk_compare_brief(old_file, new_file, genre, expect_verdict,
                      expect_unmeasured_lines=None, expect_code=None,
                      break_ffmpeg=False):
    """
    Parses the --compare --brief output line by line, as a machine reading it would.

    It has to obey the same rules as the single-file brief, because the same parser reads
    both: the same four verdict words with their counts, a cap of 20 lines with whatever
    does not fit COUNTED instead of dropped, and not one absolute path on any line — the
    reasons inside UNMEASURED come straight out of ffmpeg and those do carry paths.
    """
    got = run_compare_cli(old_file, new_file, genre, brief=True, break_ffmpeg=break_ffmpeg)
    lines = [ln for ln in got["stdout"].splitlines() if ln.strip()]
    assert lines, "%s -> %s: --brief printed nothing. stderr: %s" % (
        old_file, new_file, _one_line(got["stderr"], 200))
    assert len(lines) <= 20, "the compare brief came out with %d lines, the cap is 20" % (
        len(lines),)

    head = lines[0]
    assert head.startswith("AISINESTES COMPARE %s -> %s |" % (old_file, new_file)), (
        "the first line is not 'AISINESTES COMPARE <old> -> <new> |' -> %r" % head)
    for line in lines:
        hint = absolute_path_hint(line)
        assert hint is None, (
            "the compare brief is leaking an absolute path (%r) -> %r" % (hint, line))
    assert lines[1].startswith("VERDICT: %s" % expect_verdict), (
        "expected 'VERDICT: %s' and got %r" % (expect_verdict, lines[1]))
    assert "| old:" in lines[1], (
        "the verdict line does not report the old side -> %r" % lines[1])
    assert any(ln.startswith("files: ") for ln in lines), (
        "the compare brief has no 'files:' line -> %r" % lines)
    assert lines[-1] == "exit=%d" % got["code"], (
        "the last brief line is %r and the process returned %d — a brief that lies about "
        "its own exit code is worse than no brief" % (lines[-1], got["code"]))

    unmeasured = [ln for ln in lines if ln.startswith("UNMEASURED ")]
    if expect_unmeasured_lines is not None:
        assert len(unmeasured) == expect_unmeasured_lines, (
            "%d UNMEASURED lines expected in the compare brief, there are %d -> %s"
            % (expect_unmeasured_lines, len(unmeasured),
               _one_line(" / ".join(unmeasured), 300)))
    if expect_code is not None:
        assert got["code"] == expect_code, (
            "the CLI returned %d and %d was expected" % (got["code"], expect_code))
    return "%s -> %s (%s): %s | %d UNMEASURED | %d lines | exit %d" % (
        old_file, new_file, genre, lines[1], len(unmeasured), len(lines), got["code"])


def chk_compare_missing(file_name, genre, expect_delta):
    """
    The case of a metric that exists on one side and not on the other.

    It reproduces something real: the same file measured twice, the second time on a
    machine with no ffmpeg, so the loudness is gone. What must NEVER come out of that is
    a delta: a zero there would read as "it did not change" when the truth is "nobody
    knows". The negative mode demands exactly that zero.
    """
    old = copy.deepcopy(analyzed(file_name, genre))
    assert old.get("loudness"), (
        "%s: without real loudness on the old side this case proves nothing" % file_name)
    new = copy.deepcopy(old)
    new["loudness"] = None
    new["unmeasured"] = list(new.get("unmeasured") or []) + [
        "loudness (ebur128): absence reproduced by the harness"]
    new["checks"] = MODULES["targets"].evaluate(new, genre)

    cmp = MODULES["compare"].compare(old, new)
    affected = [m for m in cmp["metrics"] if m["old"] is not None and m["new"] is None]
    assert affected, (
        "no metric lost its value on the new side, so there is nothing to prove here: %s"
        % _cmp_summary(cmp))
    for m in affected:
        assert m["direction"] == "not_comparable", (
            "%s: one side is missing and direction says %r" % (m["name"], m["direction"]))
        if expect_delta is None:
            assert m["delta"] is None, "%s: delta %r where there should be none" % (
                m["name"], m["delta"])
        else:
            assert m["delta"] == expect_delta, "%s: delta %r, expected %r" % (
                m["name"], m["delta"], expect_delta)
    return "%s (%s): %d metrics with a side missing -> %s" % (
        file_name, genre, len(affected),
        ", ".join("%s delta=%r" % (m["name"], m["delta"]) for m in affected))


def chk_compare_cli(old_file, new_file, genre, expect_code, expect_new_flags=None):
    """
    Runs the real comparison in a child process and checks that it gates on the NEW file.

    The three output files have to be there and the exit code has to be explainable from
    compare.json: a gate whose code cannot be traced back to the report is a gate nobody
    can trust.
    """
    got = run_compare_cli(old_file, new_file, genre)
    report = got["report"]
    assert report is not None, ("%s -> %s: no compare.json was written in %s. stderr: %s"
                                % (old_file, new_file, got["folder"],
                                   _one_line(got["stderr"], 200)))
    for name in ("compare.txt", "compare.json", "compare.html"):
        assert os.path.exists(os.path.join(got["folder"], name)), (
            "%s -> %s: %s was not written" % (old_file, new_file, name))

    verdict = report.get("verdict")
    assert verdict is not None, "compare.json has no 'verdict' block (keys: %s)" % sorted(
        report)
    assert got["code"] == expect_code, (
        "%s -> %s (%s): the CLI returned %d and %d was expected. new=%s FLAG of %s, "
        "old=%s FLAG of %s" % (old_file, new_file, genre, got["code"], expect_code,
                               report["new"]["flags"], report["new"]["checks"],
                               report["old"]["flags"], report["old"]["checks"]))
    assert verdict["exit_code"] == got["code"], (
        "compare.json says exit_code %r and the process returned %d"
        % (verdict["exit_code"], got["code"]))
    # The gate follows the NEW file: its flags, not the old one's, are what the code says.
    assert verdict["flags"] == report["new"]["flags"], (
        "the verdict counts %d FLAG and the new file has %d: the gate is reading the "
        "wrong side" % (verdict["flags"], report["new"]["flags"]))
    if expect_new_flags is not None:
        assert report["new"]["flags"] == expect_new_flags, (
            "%s: the new file has %d FLAG and %d were expected"
            % (new_file, report["new"]["flags"], expect_new_flags))
    assert isinstance(report.get("errors"), list), (
        "compare.json has no 'errors' list: a failure of the derived artifacts would only "
        "ever exist in a line of stderr that already scrolled past (keys: %s)"
        % sorted(report))
    for side in ("old", "new"):
        for text in (report[side]["file"], report[side]["label"]):
            assert os.sep not in text and "/" not in text, (
                "compare.json is carrying a path instead of a basename: %r" % text)
    return "%s -> %s (%s): exit %d, old %d FLAG / new %d FLAG" % (
        old_file, new_file, genre, got["code"], report["old"]["flags"],
        report["new"]["flags"])


_DATA_URI_RE = re.compile(r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def _strip_data_uris(text):
    """Replaces the base64 payloads with a marker before searching the markup.

    Not cosmetic: a few hundred kB of base64 can contain any four letters by chance, so
    looking for "http" in the raw text would fail at random. What has to be checked is
    the MARKUP, and this is what is left of it once the payloads are out of the way.
    """
    return _DATA_URI_RE.sub("data:image/png;base64,<payload>", text)


def chk_html(kind, file_name, genre, other=None, must_contain=(), must_not_contain=(),
             expect_data_uris=None, path_check="absolute"):
    """
    Checks a real HTML page written by the CLI.

    Two properties are non-negotiable and both are checked on the file on disk:

      - SELF-CONTAINED: not one external request. Every src= is a data: URI and there is
        no <script>, <link>, @import, url() or iframe anywhere in the markup.
      - NO PATHS: the page names the audio by basename and nothing else. It is the output
        meant to be shared, and a local path is not the sharer's to publish.

    path_check="absolute" asserts the absolute path of the signal is NOT in the page (the
    real property). path_check="basename" asserts the BASENAME is not there either, which
    is false — the negative mode uses it to prove that this detector can actually fail
    when the string it looks for IS present.
    """
    if kind == "report":
        got = run_cli(file_name, genre)
        page = os.path.join(got["folder"], "report.html")
    else:
        got = run_compare_cli(file_name, other, genre)
        page = os.path.join(got["folder"], "compare.html")
    assert os.path.exists(page), "%s was not written. stderr: %s" % (
        page, _one_line(got["stderr"], 200))

    raw = open(page, "rb").read()
    assert raw, "%s came out empty" % os.path.basename(page)
    text = raw.decode("utf-8")          # not valid UTF-8 -> this blows up, as it should
    markup = _strip_data_uris(text)

    assert text.lstrip().lower().startswith("<!doctype html"), (
        "%s does not start with a doctype -> %r" % (page, text[:40]))
    assert "</html>" in text, "%s has no closing </html>" % page

    for token in ("http:", "https:", "//", "<script", "<link", "@import", "url(",
                  "<iframe", "xlink:href"):
        assert token not in markup.lower(), (
            "%s has %r in its markup: the page has to be self-contained, with zero "
            "external requests" % (os.path.basename(page), token))

    sources = [s for s in markup.split('src="')[1:]]
    for source in sources:
        assert source.startswith("data:"), (
            "%s has a src that is not a data URI -> %r" % (page, source[:60]))
    if expect_data_uris is not None:
        assert len(sources) == expect_data_uris, (
            "%s has %d embedded images and %d were expected"
            % (os.path.basename(page), len(sources), expect_data_uris))

    forbidden = signal_path(file_name) if path_check == "absolute" else file_name
    assert forbidden not in text, (
        "%s contains %r and it must not" % (os.path.basename(page), forbidden))
    hint = absolute_path_hint(markup)
    assert hint is None, (
        "%s is leaking an absolute path (%r found)"
        % (os.path.basename(page), hint))

    for token in must_contain:
        assert token in text, "%s does not contain %r" % (os.path.basename(page), token)
    for token in must_not_contain:
        assert token not in text, "%s contains %r and it must not" % (
            os.path.basename(page), token)

    return "%s: %d bytes, %d embedded image(s), %d bytes of markup, zero external refs" % (
        os.path.basename(page), len(raw), len(sources), len(markup))


HOSTILE_TOKENS = ("<script", "<link", "@import", "url(", "<iframe", "xlink:href",
                  "http:", "https:")


def chk_html_escaping(hostile_name, genre, require_raw_name=False):
    """
    A file name carrying HTML metacharacters has to come out ESCAPED on the page.

    The page is built by string concatenation, so the one thing standing between a file
    name and broken (or injected) markup is the escaping — and the name is the single
    piece of attacker-controlled text that is SUPPOSED to be shown, which is exactly why
    it deserves its own case. The name is created at runtime, used, and deleted: nothing
    with a hostile name stays in the repository.

    require_raw_name=True demands the unescaped name on the page, which is the bug this
    guards against: the negative mode uses it to prove the assertion can fail.
    """
    work = os.path.join(CLI_OUT, "hostile")
    out_dir = os.path.join(work, "out")
    copy_path = os.path.join(work, hostile_name)
    text = None
    stderr = ""
    code = None
    try:
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        shutil.copyfile(signal_path("fx_roto.wav"), copy_path)
        proc = subprocess.run(
            [sys.executable, "-m", "aisinestes", copy_path, "--genre", genre,
             "--out", out_dir],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=CLI_TIMEOUT)
        code = proc.returncode
        stderr = proc.stderr.decode("utf-8", errors="replace")
        page = os.path.join(out_dir, os.path.splitext(hostile_name)[0], "report.html")
        if os.path.exists(page):
            text = open(page, "rb").read().decode("utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    assert text is not None, (
        "no report.html was written for %r (exit %r). stderr: %s"
        % (hostile_name, code, _one_line(stderr, 200)))

    escaped = hostile_name.replace("&", "&amp;").replace("'", "&#x27;")
    markup = _strip_data_uris(text)
    for token in HOSTILE_TOKENS:
        assert token not in markup.lower(), (
            "the page for %r has %r in its markup" % (hostile_name, token))
    assert escaped in text, (
        "the escaped name %r is not on the page (it has to be shown, escaped)" % escaped)
    if require_raw_name:
        assert hostile_name in text, (
            "the raw name %r is not on the page" % hostile_name)
    else:
        assert hostile_name not in text, (
            "the RAW name %r is on the page: a file name is text, and text that reaches "
            "markup unescaped is how a name becomes a tag" % hostile_name)
    return "%r -> escaped as %r, %d bytes, zero external refs, copy deleted" % (
        hostile_name, escaped, len(text))


def _verdict_summary(verdict):
    return "flags=%s/%s, unmeasured=%d" % (
        verdict.get("flags"), verdict.get("checks"),
        len(verdict.get("unmeasured") or []))


def _fmt_bands(bb):
    return ", ".join("%s=%.2f" % (n, float(bb.get(n, 0.0)))
                     for n, _l, _h in ms.CONTRACT_BANDS)


def _fmt_times(ts):
    if not ts:
        return "[]"
    ts = list(ts)
    if len(ts) <= 6:
        return "[" + ", ".join("%.4f" % t for t in ts) + "]"
    return "[%s, ... , %s] (%d)" % (", ".join("%.4f" % t for t in ts[:3]),
                                    ", ".join("%.4f" % t for t in ts[-2:]), len(ts))


# ---------------------------------------------------------------------------
# Case registry
# ---------------------------------------------------------------------------
# id · family · required modules · function · positive kwargs · negative kwargs
# The negative kwargs are the SAME function with the wrong expectation (or signal).

WRONG_BANDS = [(n, lo, hi) for n, lo, hi in ms.CONTRACT_BANDS]
WRONG_BANDS[0] = ("sub", 20.0, 80.0)    # deliberately wrong expectation for the negative

CASES = [
    dict(id="c00-contract-bands", family="contract", req=("analyze",),
         fn=chk_contract_bands,
         pos=dict(expected=ms.CONTRACT_BANDS),
         neg=dict(expected=WRONG_BANDS),
         neg_note="band expectation tampered with (sub up to 80 Hz)"),

    dict(id="b01-sine50-sub", family="bands", req=("wavio", "analyze"), fn=chk_band,
         pos=dict(file_name="sine_50hz.wav", band="sub", min_pct=90.0),
         neg=dict(file_name="sine_50hz.wav", band="mids", min_pct=90.0),
         neg_note="a 50 Hz sine is asked to sit in 'mids'"),

    dict(id="b02-sine1k-mids", family="bands", req=("wavio", "analyze"), fn=chk_band,
         pos=dict(file_name="sine_1khz.wav", band="mids", min_pct=90.0),
         neg=dict(file_name="sine_1khz.wav", band="sub", min_pct=90.0),
         neg_note="a 1 kHz sine is asked to sit in 'sub'"),

    # WATCH OUT: 10 kHz falls in 'air' (8000-16000) according to the CONTRACT BANDS, NOT
    # in 'high_mids' (2000-8000). Measured with the harness's independent FFT: 100 % air.
    dict(id="b03-sine10k-air", family="bands", req=("wavio", "analyze"), fn=chk_band,
         pos=dict(file_name="sine_10khz.wav", band="air", min_pct=85.0),
         neg=dict(file_name="sine_10khz.wav", band="high_mids", min_pct=85.0),
         neg_note="a 10 kHz sine is asked to sit in 'high_mids' (2-8 kHz)"),

    dict(id="b04-sum-100", family="bands", req=("wavio", "analyze"), fn=chk_band_sum,
         pos=dict(file_name="white_noise.wav", expected=100.0, tol=1.0),
         neg=dict(file_name="white_noise.wav", expected=50.0, tol=1.0),
         neg_note="the bands are required to add up to 50"),

    dict(id="n01-sine1k-levels", family="levels", req=("wavio", "analyze"),
         fn=chk_levels,
         pos=dict(file_name="sine_1khz.wav", peak_db=-20.0, tol_peak=0.3,
                  rms_db=-23.0, tol_rms=0.5),
         neg=dict(file_name="sine_1khz.wav", peak_db=-6.0, tol_peak=0.3,
                  rms_db=-9.0, tol_rms=0.5),
         neg_note="-6 dBFS is expected from a sine that sits at -20"),

    dict(id="r01-white-noise", family="noise", req=("wavio", "analyze"),
         fn=chk_white_noise,
         pos=dict(file_name="white_noise.wav", tol_rel=0.35, tol_abs=3.0),
         neg=dict(file_name="sine_1khz.wav", tol_rel=0.35, tol_abs=3.0),
         neg_note="a pure sine is required to have the split of white noise"),

    dict(id="s01-silence", family="silence", req=("wavio", "analyze"), fn=chk_silence,
         pos=dict(file_name="silence.wav", peak_max=0.0, peak_db_max=-80.0, onsets_max=0),
         neg=dict(file_name="white_noise.wav", peak_max=0.0, peak_db_max=-80.0, onsets_max=0),
         neg_note="'there is no audio' is asked of a file with noise at -12 dBFS"),

    dict(id="t01-bpm-132", family="tempo", req=("wavio", "analyze"), fn=chk_bpm,
         timeout=90.0,
         pos=dict(file_name="clicks_132bpm.wav", lo=130.0, hi=134.0),
         neg=dict(file_name="clicks_132bpm.wav", lo=120.0, hi=124.0),
         neg_note="the range of the old bug (122 BPM) is accepted; with a real 132 it must fail"),

    dict(id="t02-two-clicks", family="tempo", req=("wavio", "analyze"),
         fn=chk_onsets_count,
         pos=dict(file_name="two_clicks_100ms.wav", expected=2),
         neg=dict(file_name="two_clicks_100ms.wav", expected=5),
         neg_note="5 onsets are expected where there are 2"),

    dict(id="t03-spacing", family="tempo", req=("wavio", "analyze"),
         fn=chk_onset_spacing,
         pos=dict(file_name="two_clicks_100ms.wav", expected_sep_ms=100.0, tol_ms=10.0),
         neg=dict(file_name="two_clicks_100ms.wav", expected_sep_ms=250.0, tol_ms=10.0),
         neg_note="a spacing of 250 ms is expected where there is 100"),

    dict(id="f01-float32", family="format", req=("wavio", "analyze"),
         fn=chk_equivalence,
         pos=dict(file_a="sine_1khz_float32.wav", file_b="sine_1khz.wav",
                  tol_db=0.1, tol_band_pct=0.5),
         neg=dict(file_a="sine_1khz_float32.wav", file_b="sine_50hz.wav",
                  tol_db=0.1, tol_band_pct=0.5),
         neg_note="the 1 kHz float32 is compared against the 50 Hz sine"),

    # Expected LUFS-I = -23, not -20: the sine has its PEAK at -20 dBFS and LUFS is an
    # RMS measurement (-20 - 3.01). The one worth -20 is the true peak. See chk_loudness.
    dict(id="l01-lufs-1khz", family="loudness", req=("ffreport", "wavio", "analyze"),
         fn=chk_loudness, ffmpeg=True,
         pos=dict(file_name="sine_1khz.wav", lufs_expected=-23.0, tol=1.0),
         neg=dict(file_name="sine_1khz.wav", lufs_expected=-40.0, tol=1.0),
         neg_note="-40 LUFS is expected from a signal that sits at -23"),

    dict(id="l02-true-peak", family="loudness", req=("ffreport", "wavio", "analyze"),
         fn=chk_loudness, ffmpeg=True,
         pos=dict(file_name="sine_1khz.wav", lufs_expected=-23.0, tol=1.0,
                  tp_expected=-20.0, tol_tp=0.5),
         neg=dict(file_name="sine_1khz.wav", lufs_expected=-23.0, tol=1.0,
                  tp_expected=-3.0, tol_tp=0.5),
         neg_note="a true peak of -3 dBFS is expected where it is worth -20"),

    # The real cross-check: two independent instruments (ffmpeg/ebur128 and our own
    # FFT+RMS) have to give the same number for a 1 kHz sine.
    dict(id="l03-cross-fft-ffmpeg", family="loudness", req=("ffreport", "wavio", "analyze"),
         fn=chk_loudness, ffmpeg=True,
         pos=dict(file_name="sine_1khz.wav", lufs_expected=-23.0, tol=1.0, tol_vs_rms=0.5),
         neg=dict(file_name="sine_1khz.wav", lufs_expected=-23.0, tol=1.0, tol_vs_rms=0.0001),
         neg_note="exact agreement to 0.0001 dB is demanded between the two instruments"),

    # 'sub' and 'bite' are the substrings of the labels targets.py emits
    # ("Sub magnitude (20-60 Hz)" and "Bite magnitude (2000 Hz and up)").
    dict(id="x01-fx-broken", family="fx", req=("targets", "wavio", "analyze"),
         fn=chk_fx_flags,
         pos=dict(file_name="fx_roto.wav", genre="fx-impact",
                  required_flags=("sub", "bite")),
         neg=dict(file_name="fx_bueno.wav", genre="fx-impact",
                  required_flags=("sub", "bite")),
         neg_note="sub and bite FLAGs are demanded on the HEALTHY impact"),

    dict(id="x02-fx-good", family="fx", req=("targets", "wavio", "analyze"),
         fn=chk_fx_flags,
         pos=dict(file_name="fx_bueno.wav", genre="fx-impact", no_flags=True),
         neg=dict(file_name="fx_roto.wav", genre="fx-impact", no_flags=True),
         neg_note="zero FLAGs are demanded on the BROKEN impact (98 % in sub)"),

    # --- Exit codes: 0 measured-and-clean · 1 flag · 3 clean-but-incomplete ---------
    # fx-impact is used because none of its four checks needs ffmpeg: with ffmpeg out of
    # the picture the checks still all come out OK and what is left is exactly the case
    # code 3 exists for — nothing to complain about AMONG WHAT WAS MEASURED.
    dict(id="e01-exit3-incomplete", family="exitcode",
         req=("targets", "wavio", "analyze", "ffreport"), fn=chk_cli_exit, timeout=120.0,
         pos=dict(file_name="fx_bueno.wav", genre="fx-impact", break_ffmpeg=True,
                  expect_code=3, expect_flags=0, expect_unmeasured=True),
         neg=dict(file_name="fx_bueno.wav", genre="fx-impact", break_ffmpeg=True,
                  expect_code=0, expect_flags=0, expect_unmeasured=True),
         neg_note="exit 0 is demanded from a run where loudness could not be measured"),

    # The mirror image, and the one that proves the 3 is not a constant: the SAME file and
    # the SAME profile, with ffmpeg working, have to come out 0 with nothing unmeasured.
    dict(id="e02-exit0-complete", family="exitcode",
         req=("targets", "wavio", "analyze", "ffreport"), fn=chk_cli_exit, ffmpeg=True,
         timeout=120.0,
         pos=dict(file_name="fx_bueno.wav", genre="fx-impact",
                  expect_code=0, expect_flags=0, expect_unmeasured=False),
         neg=dict(file_name="fx_bueno.wav", genre="fx-impact",
                  expect_code=3, expect_flags=0, expect_unmeasured=False),
         neg_note="exit 3 (incomplete) is demanded from a run where everything WAS measured"),

    # Precedence: with FLAGs *and* unmeasured metrics at the same time, 1 wins over 3.
    dict(id="e03-flag-wins", family="exitcode",
         req=("targets", "wavio", "analyze", "ffreport"), fn=chk_cli_exit, timeout=120.0,
         pos=dict(file_name="fx_roto.wav", genre="fx-impact", break_ffmpeg=True,
                  expect_code=1, expect_flags=3, expect_unmeasured=True),
         neg=dict(file_name="fx_roto.wav", genre="fx-impact", break_ffmpeg=True,
                  expect_code=3, expect_flags=3, expect_unmeasured=True),
         neg_note="exit 3 is demanded where there are 3 FLAGs (the flag has to win)"),

    # --- --brief: the format is parsed line by line, as an agent would --------------
    dict(id="q01-brief-flag", family="brief",
         req=("targets", "wavio", "analyze", "ffreport"), fn=chk_brief, timeout=120.0,
         pos=dict(file_name="fx_roto.wav", genre="fx-impact",
                  expect_verdict="FLAG", expect_flag_lines=3, expect_code=1),
         neg=dict(file_name="fx_bueno.wav", genre="fx-impact",
                  expect_verdict="FLAG", expect_flag_lines=3, expect_code=1),
         neg_note="a FLAG verdict with 3 FLAG lines is demanded of the HEALTHY impact"),

    # CLEAN, unlike INCOMPLETE, requires everything to have been measured: this one needs
    # a real ffmpeg, and without it the case is skipped instead of coming out green.
    dict(id="q02-brief-clean", family="brief",
         req=("targets", "wavio", "analyze", "ffreport"), fn=chk_brief, ffmpeg=True,
         timeout=120.0,
         pos=dict(file_name="fx_bueno.wav", genre="fx-impact",
                  expect_verdict="CLEAN", expect_flag_lines=0, expect_code=0),
         neg=dict(file_name="fx_roto.wav", genre="fx-impact",
                  expect_verdict="CLEAN", expect_flag_lines=0, expect_code=0),
         neg_note="a CLEAN brief with zero FLAG lines is demanded of the BROKEN impact"),

    # Genre "none" judges nothing: the brief must say NOT JUDGED instead of claiming a
    # CLEAN pass nobody ever tested for. Needs real ffmpeg (without it the same run is
    # INCOMPLETE, which is a different true statement).
    dict(id="n01-brief-not-judged", family="brief",
         req=("targets", "wavio", "analyze", "ffreport"), fn=chk_brief, ffmpeg=True,
         timeout=120.0,
         pos=dict(file_name="fx_bueno.wav", genre="none",
                  expect_verdict="NOT JUDGED", expect_flag_lines=0, expect_code=0),
         neg=dict(file_name="fx_bueno.wav", genre="none",
                  expect_verdict="CLEAN", expect_flag_lines=0, expect_code=0),
         neg_note="a CLEAN verdict is demanded where nothing was judged (genre none)"),

    # Digital silence has no band SHARES (0/0 is not 0 %): the impact gate must flag all
    # four checks as unmeasurable instead of waving "0.00 % OK" rows through.
    dict(id="s02-silence-gate", family="silence",
         req=("targets", "wavio", "analyze", "ffreport"), fn=chk_cli_exit, timeout=120.0,
         pos=dict(file_name="silence.wav", genre="fx-impact",
                  expect_code=1, expect_flags=4),
         neg=dict(file_name="silence.wav", genre="fx-impact",
                  expect_code=1, expect_flags=0),
         neg_note="zero flags are demanded of pure silence under the impact gate"),

    # The oldest error route, now guarded: a missing input file is exit 2 with a clear
    # message, never a traceback and never a half report.
    dict(id="err01-missing-file", family="errors",
         req=("targets", "wavio", "analyze", "ffreport"), fn=chk_missing_file,
         timeout=60.0,
         pos=dict(expect_code=2),
         neg=dict(expect_code=0),
         neg_note="exit 0 is demanded from a run whose input file does not exist"),

    # --- --compare: the direction of each metric, by the MEANING of its check ---------
    # fx_roto -> fx_bueno: everything that was wrong gets fixed. The negative asks for
    # the opposite directions: a comparison that cannot tell an improvement from a
    # regression would pass both, and then it is measuring nothing.
    dict(id="y01-compare-directions", family="compare",
         req=("compare", "pipeline", "targets", "wavio", "analyze"), fn=chk_compare,
         timeout=90.0,
         pos=dict(old_file="fx_roto.wav", new_file="fx_bueno.wav", genre="fx-impact",
                  expect={"Sub magnitude": ("improved", "fixed"),
                          "Body magnitude": ("improved", "fixed"),
                          "Bite magnitude": ("improved", "fixed"),
                          "Fast attack": ("unchanged", "still_ok")}),
         neg=dict(old_file="fx_roto.wav", new_file="fx_bueno.wav", genre="fx-impact",
                  expect={"Sub magnitude": ("worsened", "broke"),
                          "Body magnitude": ("worsened", "broke"),
                          "Bite magnitude": ("worsened", "broke"),
                          "Fast attack": ("unchanged", "still_ok")}),
         neg_note="the directions are inverted: the three fixed metrics are required "
                  "to read as regressions"),

    # ⭐ THE CASE THIS FEATURE EXISTS FOR: a round of fixes that costs you a metric.
    # fx_roto -> fx_swell repairs the whole spectral balance (sub, body and bite all come
    # back into range) and breaks the attack, which nobody was watching. The comparison
    # has to show BOTH things at once — that is what `require_mixed` demands.
    dict(id="y02-compare-regression", family="compare",
         req=("compare", "pipeline", "targets", "wavio", "analyze"), fn=chk_compare,
         timeout=90.0,
         pos=dict(old_file="fx_roto.wav", new_file="fx_swell.wav", genre="fx-impact",
                  require_mixed=True,
                  expect={"Sub magnitude": ("improved", "fixed"),
                          "Body magnitude": ("improved", "fixed"),
                          "Bite magnitude": ("improved", "fixed"),
                          "Fast attack": ("worsened", "broke")}),
         neg=dict(old_file="fx_roto.wav", new_file="fx_swell.wav", genre="fx-impact",
                  require_mixed=True,
                  expect={"Sub magnitude": ("improved", "fixed"),
                          "Fast attack": ("improved", "fixed")}),
         neg_note="the metric that BROKE (the attack) is required to read as fixed"),

    # A side that was never measured is not a zero. techno-club is used because its
    # loudness checks are the ones that disappear without ffmpeg.
    dict(id="y03-compare-missing-side", family="compare",
         req=("compare", "pipeline", "targets", "wavio", "analyze", "ffreport"),
         fn=chk_compare_missing, ffmpeg=True, timeout=90.0,
         pos=dict(file_name="fx_bueno.wav", genre="techno-club", expect_delta=None),
         neg=dict(file_name="fx_bueno.wav", genre="techno-club", expect_delta=0.0),
         neg_note="a delta of 0.0 is demanded from a metric that was never measured"),

    # The comparison gates on the NEW file: same two signals, swapped round, opposite
    # exit code. Run as a real child process, because the gate IS the exit code.
    dict(id="y04-compare-gate-clean", family="compare",
         req=("compare", "htmlreport", "pipeline", "targets", "wavio", "analyze",
              "ffreport"), fn=chk_compare_cli, timeout=120.0,
         pos=dict(old_file="fx_roto.wav", new_file="fx_bueno.wav", genre="fx-impact",
                  expect_code=0, expect_new_flags=0),
         neg=dict(old_file="fx_roto.wav", new_file="fx_bueno.wav", genre="fx-impact",
                  expect_code=1, expect_new_flags=0),
         neg_note="exit 1 is demanded from a comparison whose NEW file is clean"),

    dict(id="y05-compare-gate-flag", family="compare",
         req=("compare", "htmlreport", "pipeline", "targets", "wavio", "analyze",
              "ffreport"), fn=chk_compare_cli, timeout=120.0,
         pos=dict(old_file="fx_bueno.wav", new_file="fx_roto.wav", genre="fx-impact",
                  expect_code=1, expect_new_flags=3),
         neg=dict(old_file="fx_bueno.wav", new_file="fx_roto.wav", genre="fx-impact",
                  expect_code=0, expect_new_flags=3),
         neg_note="exit 0 is demanded from a comparison whose NEW file has 3 FLAGs "
                  "(the old one being clean must not save it)"),

    # The third kind of reference: a RANGE, where "better" is neither up nor down but
    # closer. Under techno-club both files sit far below the sub window, so moving up is
    # an improvement that is still a FLAG — a state no other case reaches — while the
    # true peak (a ceiling) goes the other way and breaks.
    dict(id="y06-compare-range", family="compare",
         req=("compare", "pipeline", "targets", "wavio", "analyze", "ffreport"),
         fn=chk_compare, ffmpeg=True, timeout=120.0,
         pos=dict(old_file="white_noise.wav", new_file="fx_bueno.wav",
                  genre="techno-club", require_mixed=True,
                  expect={"Sub magnitude": ("improved", "still_flag"),
                          "Sub+bass magnitude": ("improved", "still_flag"),
                          "True peak": ("worsened", "broke")}),
         neg=dict(old_file="white_noise.wav", new_file="fx_bueno.wav",
                  genre="techno-club", require_mixed=True,
                  expect={"Sub magnitude": ("improved", "fixed"),
                          "True peak": ("worsened", "broke")}),
         neg_note="a metric that moved towards its window without reaching it is "
                  "required to read as 'fixed'"),

    # ⭐ The epsilon bug: a step of 0.05 that FLIPS the verdict. Both directions of the
    # crossing, on the two metrics whose whole decision window is smaller than the old
    # absolute epsilon. The negative asks for "unchanged", which is what it used to say.
    dict(id="y07-crossing-outranks-eps", family="compare",
         req=("compare", "pipeline", "targets"), fn=chk_compare_crossing, timeout=60.0,
         pos=dict(metric_name="Fast attack (envelope peak)",
                  target="<= 0.15 of duration (first 15 %)",
                  old=0.13, new=0.18, status_old="OK", status_new="FLAG",
                  expect_direction="worsened", expect_transition="broke"),
         neg=dict(metric_name="Fast attack (envelope peak)",
                  target="<= 0.15 of duration (first 15 %)",
                  old=0.13, new=0.18, status_old="OK", status_new="FLAG",
                  expect_direction="unchanged", expect_transition="broke"),
         neg_note="'unchanged' is demanded of a step that crossed the threshold and "
                  "broke the check"),

    dict(id="y08-crossing-fixed", family="compare",
         req=("compare", "pipeline", "targets"), fn=chk_compare_crossing, timeout=60.0,
         pos=dict(metric_name="Bite magnitude (2000 Hz and up)", target=">= 0.16 %",
                  old=0.13, new=0.17, status_old="FLAG", status_new="OK",
                  expect_direction="improved", expect_transition="fixed"),
         neg=dict(metric_name="Bite magnitude (2000 Hz and up)", target=">= 0.16 %",
                  old=0.13, new=0.17, status_old="FLAG", status_new="OK",
                  expect_direction="unchanged", expect_transition="fixed"),
         neg_note="'unchanged' is demanded of a step that crossed the threshold and "
                  "fixed the check"),

    # The comparison brief, parsed like the single-file one: same verdict words, same
    # cap, no absolute path anywhere. With ffmpeg out of the picture the new file comes
    # out INCOMPLETE, which is what puts an UNMEASURED line in there to check.
    dict(id="y09-compare-brief", family="compare",
         req=("compare", "htmlreport", "pipeline", "targets", "wavio", "analyze",
              "ffreport"), fn=chk_compare_brief, timeout=120.0,
         pos=dict(old_file="fx_roto.wav", new_file="fx_bueno.wav", genre="fx-impact",
                  break_ffmpeg=True, expect_verdict="INCOMPLETE (0 of 4, 1 unmeasured)",
                  expect_unmeasured_lines=1, expect_code=3),
         neg=dict(old_file="fx_roto.wav", new_file="fx_bueno.wav", genre="fx-impact",
                  break_ffmpeg=True, expect_verdict="CLEAN",
                  expect_unmeasured_lines=1, expect_code=3),
         neg_note="a CLEAN verdict is demanded of a comparison whose new file could not "
                  "measure its loudness"),

    dict(id="y10-compare-brief-not-judged", family="compare",
         req=("compare", "htmlreport", "pipeline", "targets", "wavio", "analyze",
              "ffreport"), fn=chk_compare_brief, ffmpeg=True, timeout=120.0,
         pos=dict(old_file="fx_roto.wav", new_file="fx_bueno.wav", genre="none",
                  expect_verdict='NOT JUDGED (0 checks: genre "none")',
                  expect_unmeasured_lines=0, expect_code=0),
         neg=dict(old_file="fx_roto.wav", new_file="fx_bueno.wav", genre="none",
                  expect_verdict="CLEAN", expect_unmeasured_lines=0, expect_code=0),
         neg_note="a CLEAN verdict is demanded of a comparison where nothing was judged"),

    # --- report.html / compare.html: self-contained, and no paths --------------------
    dict(id="h01-html-selfcontained", family="html",
         req=("htmlreport", "pipeline", "targets", "wavio", "analyze", "ffreport"),
         fn=chk_html, ffmpeg=True, timeout=120.0,
         pos=dict(kind="report", file_name="fx_roto.wav", genre="fx-impact",
                  expect_data_uris=2,
                  must_contain=("fx_roto.wav", ">FLAG<", "data:image/png;base64,")),
         neg=dict(kind="report", file_name="fx_roto.wav", genre="fx-impact",
                  expect_data_uris=5,
                  must_contain=("fx_roto.wav", ">FLAG<", "data:image/png;base64,")),
         neg_note="5 embedded images are demanded from a page that has 2"),

    # The privacy rule, and the proof that the detector for it can fail: with
    # path_check="basename" it looks for a string that IS on the page.
    dict(id="h02-html-no-path", family="html",
         req=("htmlreport", "pipeline", "targets", "wavio", "analyze", "ffreport"),
         fn=chk_html, ffmpeg=True, timeout=120.0,
         pos=dict(kind="report", file_name="fx_roto.wav", genre="fx-impact",
                  path_check="absolute"),
         neg=dict(kind="report", file_name="fx_roto.wav", genre="fx-impact",
                  path_check="basename"),
         neg_note="the BASENAME is required to be absent from a page that shows it"),

    dict(id="h03-compare-html", family="html",
         req=("compare", "htmlreport", "pipeline", "targets", "wavio", "analyze",
              "ffreport"), fn=chk_html, timeout=120.0,
         pos=dict(kind="compare", file_name="fx_roto.wav", other="fx_bueno.wav",
                  genre="fx-impact", expect_data_uris=0,
                  must_contain=("fx_roto.wav", "fx_bueno.wav", "improved", "fixed"),
                  must_not_contain=("worsened",)),
         neg=dict(kind="compare", file_name="fx_roto.wav", other="fx_bueno.wav",
                  genre="fx-impact", expect_data_uris=0,
                  must_contain=("fx_roto.wav", "fx_bueno.wav", "improved", "worsened")),
         neg_note="the word 'worsened' is demanded from a comparison where nothing "
                  "got worse"),

    # A file name is attacker-controlled text that the page is SUPPOSED to display. The
    # copy is made at runtime with characters Windows allows and HTML does not, used, and
    # deleted — nothing hostile is stored in the repository.
    dict(id="h04-html-escaping", family="html",
         req=("htmlreport", "pipeline", "targets", "wavio", "analyze", "ffreport"),
         fn=chk_html_escaping, timeout=120.0,
         pos=dict(hostile_name="a&b'c.wav", genre="fx-impact"),
         neg=dict(hostile_name="a&b'c.wav", genre="fx-impact", require_raw_name=True),
         neg_note="the RAW unescaped name is demanded on a page that escapes it"),
]


# ---------------------------------------------------------------------------
# Engine: per-case timeout, leaving nothing hanging
# ---------------------------------------------------------------------------

class Timeout(Exception):
    pass


def with_timeout(fn, seconds):
    """
    Runs fn() in a daemon thread and waits `seconds`. If it did not finish, it raises
    Timeout and carries on: the thread is a daemon, so it dies with the interpreter and
    no live process is left behind.
    (ThreadPoolExecutor is deliberately not used: its threads are NOT daemons and would
    block the interpreter from exiting if a case hangs.)
    """
    box = {}

    def worker():
        try:
            box["r"] = fn()
            box["ok"] = True
        except BaseException as e:      # noqa: BLE001 - re-raised as is on the main thread
            box["e"] = e
            box["ok"] = False

    h = threading.Thread(target=worker, daemon=True)
    h.start()
    h.join(seconds)
    if h.is_alive():
        raise Timeout("the case did not finish in %.0f s" % seconds)
    if box.get("ok"):
        return box["r"]
    raise box["e"]


def missing_modules(req):
    return [m for m in req if MODULES.get(m) is None]


def run_case(case, mode, t0):
    """Returns (status, detail)."""
    if time.monotonic() - t0 > TOTAL_BUDGET:
        return "NOT RUN", "time budget exhausted (%.0f s)" % TOTAL_BUDGET

    missing = missing_modules(case["req"])
    if missing:
        det = ", ".join("%s (%s)" % (m, MISSING_REASON.get(m, "?")) for m in missing)
        return "NOT RUN", "missing module: " + det
    if case.get("ffmpeg") and not has_ffmpeg():
        return "NOT RUN", "ffmpeg missing at %s" % FFMPEG

    kwargs = case[mode]
    fn = case["fn"]
    tout = case.get("timeout", DEFAULT_TIMEOUT)

    try:
        detail = with_timeout(lambda: fn(**kwargs), tout)
    except Timeout as e:
        return "TIMEOUT", str(e)
    except AssertionError as e:
        if mode == "neg":
            return "PASS", "failed as it had to -> %s" % _one_line(str(e))
        return "FAIL", _one_line(str(e))
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, _one_line(str(e)))
        if mode == "neg":
            # It blew up before reaching the assertion: the negative proved nothing.
            return "INCONCLUSIVE", "exception before the assertion -> " + msg
        return "FAIL", msg

    if mode == "neg":
        return "FAIL", ("HARNESS DEFECT: the assertion CANNOT fail. %s -> it returned %s"
                        % (case.get("neg_note", ""), _one_line(str(detail))))
    return "PASS", _one_line(str(detail))


def _one_line(s, n=400):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + " [...]"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    requested_mode = "all"
    prefix = None
    for i, a in enumerate(argv):
        if a == "--mode" and i + 1 < len(argv):
            requested_mode = argv[i + 1]
        elif a == "--case" and i + 1 < len(argv):
            prefix = argv[i + 1]
        elif a == "--simulate-missing" and i + 1 < len(argv):
            for m in argv[i + 1].split(","):
                m = m.strip()
                if m in MODULES:
                    MODULES[m] = None
                    MISSING_REASON[m] = "absence SIMULATED by --simulate-missing"

    modes = {"all": ["pos", "neg"], "positive": ["pos"], "negative": ["neg"]}.get(
        requested_mode, ["pos", "neg"])

    print("=" * 96)
    print("run_harness.py — truth assertions (Aisinestes)")
    print("=" * 96)
    print("signals: %s" % SIG_DIR)
    print("modules:")
    for m in MODULE_NAMES:
        if MODULES[m] is not None:
            print("  aisinestes.%-10s PRESENT" % m)
        else:
            print("  aisinestes.%-10s MISSING  (%s)" % (m, MISSING_REASON.get(m, "?")))
    print("  ffmpeg%-15s %s" % ("", "PRESENT" if has_ffmpeg() else "MISSING (%s)" % FFMPEG))
    missing_signals = [v["file"] for v in ms.TRUTHS
                       if not os.path.exists(os.path.join(SIG_DIR, v["file"]))]
    if missing_signals:
        print("  MISSING SIGNALS: %s  -> run make_signals.py" % ", ".join(missing_signals))
    print("")

    t0 = time.monotonic()
    rows = []
    for case in CASES:
        if prefix and not case["id"].startswith(prefix):
            continue
        for mode in modes:
            start = time.monotonic()
            status, detail = run_case(case, mode, t0)
            rows.append((case["id"], case["family"], mode, status,
                         time.monotonic() - start, detail))

    print("%-22s %-10s %-4s %-12s %7s  %s" % ("ID", "FAMILY", "MODE", "STATUS",
                                              "sec", "DETAIL"))
    print("-" * 96)
    for cid, fam, mode, status, dt, det in rows:
        print("%-22s %-10s %-4s %-12s %7.2f  %s" % (cid, fam, mode, status, dt, det))
    print("-" * 96)

    counts = {}
    for _c, _f, _m, status, _d, _x in rows:
        counts[status] = counts.get(status, 0) + 1
    total = len(rows)
    print("SUMMARY: %d cases in %.1f s -> %s"
          % (total, time.monotonic() - t0,
             ", ".join("%s=%d" % (k, counts[k]) for k in sorted(counts))))

    defects = [f for f in rows if f[3] == "FAIL" and "HARNESS DEFECT" in f[5]]
    if defects:
        print("")
        print("HARNESS DEFECTS (assertions that cannot fail, i.e. that prove nothing):")
        for f in defects:
            print("  - %s [%s]" % (f[0], f[1]))

    if counts.get("FAIL"):
        code = 1
    elif total == 0 or counts.get("PASS", 0) != total:
        code = 2        # something was left NOT RUN / TIMEOUT / INCONCLUSIVE
    else:
        code = 0
    print("EXIT %d (%s)" % (code, {0: "all PASS", 1: "there are FAILs",
                                   2: "incomplete: something could not be run"}[code]))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
