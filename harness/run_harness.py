# -*- coding: utf-8 -*-
"""
run_harness.py — truth assertions for Aisinestes.

Runs the instrument (aisinestes.*) against the synthetic signals of make_signals.py,
about which we know EVERYTHING beforehand, and checks that it measures the truth.

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

import math
import os
import shutil
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
SIG_DIR = os.path.join(BASE, "signals")

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


for _m in ("wavio", "analyze", "ffreport", "targets"):
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
    for m in ("wavio", "analyze", "ffreport", "targets"):
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
