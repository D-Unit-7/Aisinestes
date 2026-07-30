# -*- coding: utf-8 -*-
"""
make_signals.py — generator of synthetic signals with KNOWN TRUTH for Aisinestes.

Why it exists: the instrument (aisinestes) is only worth something if it measures the
truth. To know whether it measures the truth we need material about which we know
EVERYTHING beforehand: exact frequency, exact amplitude, exact number of transients,
exact tempo. That is what this file generates.

House rules honoured here:
  - pure stdlib (wave, struct, array, math, cmath, random). Zero pip, zero network.
  - every signal comes with its declared, checkable "truth" (see TRUTHS).
  - the validation of step 4 (--only-validate) does NOT use aisinestes: it re-reads the
    WAVs with the stdlib `wave` module. The instrument that validates the generator has
    to be independent from the instrument under test.
  - the only exception is the float32 WAV: `wave` does not read IEEE float (format 3),
    so the RIFF is parsed by hand down below. It is still independent from aisinestes.

Usage:
    python make_signals.py                  # generates + validates + measures the spectrum
    python make_signals.py --only-validate  # does not regenerate, only re-reads and validates
    python make_signals.py --no-spectrum    # skips the spectral measurement (faster)

Exit code: 0 if every signal exists and holds its declared truth; 1 if any does not.
"""

import array
import cmath
import math
import os
import random
import struct
import sys
import wave

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RATE = 44100                      # Hz, fixed by the contract
BASE = os.path.dirname(os.path.abspath(__file__))
SIG_DIR = os.path.join(BASE, "signals")

# Fixed seeds: without these the noise signals would not be reproducible and the harness
# would measure something different on every run (i.e. it would measure nothing).
SEED_NOISE = 20260727
SEED_CLICK = 1337
SEED_CRACK = 4242

# CONTRACT bands. They are replicated here on purpose, so it can be checked that the
# analyze.py module declares EXACTLY these ones (if the harness imported BANDS from
# analyze, an error in analyze would become invisible: the test would adapt to the bug).
CONTRACT_BANDS = [
    ("sub", 20.0, 60.0),
    ("bass", 60.0, 120.0),
    ("low_mids", 120.0, 350.0),
    ("mids", 350.0, 2000.0),
    ("high_mids", 2000.0, 8000.0),
    ("air", 8000.0, 16000.0),
]

# Gains of the layers of the "good" impact. They are kept as constants so the whole thing
# can be recalibrated in a single place if the fx-impact thresholds change.
# Calibrated by measuring with this file's own FFT until the split came out comfortably
# clear of the contract thresholds (sub <= 60 %, body >= 15 %, crack >= 10 %):
# the goal is sub ~35 % / body ~35 % / crack ~30 %, i.e. margin on all three.
# Since the peak is normalized afterwards, what matters is the RATIO between the three.
FXB_GAIN_THUD = 0.50      # 45 Hz
FXB_GAIN_BODY = 0.51      # 800 Hz + 1300 Hz
FXB_GAIN_CRACK = 2.03     # noise with emphasis around ~5 kHz


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------

def _n_samples(dur_s):
    """Number of frames for a duration in seconds (exact integer)."""
    return int(round(dur_s * RATE))


def _sine(freq, dur_s, amp, phase=0.0):
    """Pure sine. amp is the linear peak amplitude (1.0 = full scale)."""
    n = _n_samples(dur_s)
    w = 2.0 * math.pi * freq / RATE
    return [amp * math.sin(w * i + phase) for i in range(n)]


def _decay_exp(xs, tau_s):
    """Exponential envelope e^(-t/tau) applied in-place."""
    k = -1.0 / (tau_s * RATE)
    for i in range(len(xs)):
        xs[i] *= math.exp(k * i)
    return xs


def _attack(xs, ms=2.0):
    """
    Raised-cosine fade-in of `ms` milliseconds, in-place.

    It is there so the start is not a step: a step injects a broadband click that would
    dirty the spectral balance the signal claims to have. 2 ms is still a fast attack
    (the fx-impact check asks for the peak inside the first 15 %).
    """
    n = max(1, int(round(ms * RATE / 1000.0)))
    n = min(n, len(xs))
    for i in range(n):
        xs[i] *= 0.5 - 0.5 * math.cos(math.pi * i / n)
    return xs


def _mix(dest, src, offset=0):
    """Adds `src` into `dest` starting at `offset` (truncates if it overruns)."""
    n = min(len(src), len(dest) - offset)
    for i in range(n):
        dest[offset + i] += src[i]
    return dest


def _normalize_peak(xs, peak=0.9):
    """Scales the whole signal so that its absolute peak lands on `peak`.

    It is a global scaling: it does NOT change the spectral balance (which is the truth
    these signals have to hold up), only the level.
    """
    m = max((abs(v) for v in xs), default=0.0)
    if m <= 0.0:
        return xs
    g = peak / m
    for i in range(len(xs)):
        xs[i] *= g
    return xs


def _bandpass_biquad(xs, f0, q):
    """
    Two-pole RBJ bandpass, 0 dB peak gain, applied in-place.
    It is used to give the "crack" an emphasis around 5 kHz without depending on scipy.
    """
    w0 = 2.0 * math.pi * f0 / RATE
    alpha = math.sin(w0) / (2.0 * q)
    cw = math.cos(w0)
    b0, b1, b2 = alpha, 0.0, -alpha
    a0, a1, a2 = 1.0 + alpha, -2.0 * cw, 1.0 - alpha
    b0, b1, b2 = b0 / a0, b1 / a0, b2 / a0
    a1, a2 = a1 / a0, a2 / a0
    x1 = x2 = y1 = y2 = 0.0
    for i in range(len(xs)):
        x0 = xs[i]
        y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, x0
        y2, y1 = y1, y0
        xs[i] = y0
    return xs


def _click(amp, dur_ms, tau_ms, rnd):
    """
    Short broadband transient: random ±1 signs with exponential decay.

    The first sample is worth exactly +amp, so the peak of the signal is known and
    exact. Randomly alternating signs = flat spectrum: it is the worst case for an
    onset detector that "swallows" transients, which is exactly what has to be tested.
    """
    n = max(1, int(round(dur_ms * RATE / 1000.0)))
    k = -1.0 / (tau_ms * RATE / 1000.0)
    out = [0.0] * n
    out[0] = amp
    for i in range(1, n):
        s = 1.0 if rnd.random() < 0.5 else -1.0
        out[i] = amp * s * math.exp(k * i)
    return out


# ---------------------------------------------------------------------------
# WAV writing
# ---------------------------------------------------------------------------

def write_pcm16(path, xs):
    """Writes mono PCM16 44100 Hz with the `wave` module. Returns the list of integers."""
    ints = []
    for v in xs:
        i = int(round(v * 32767.0))
        if i > 32767:
            i = 32767
        elif i < -32768:
            i = -32768
        ints.append(i)
    arr = array.array("h", ints)
    if sys.byteorder == "big":
        arr.byteswap()                      # WAV is always little-endian
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(arr.tobytes())
    return ints


def write_float32(path, xs):
    """
    Writes mono IEEE float32 (WAVE_FORMAT_IEEE_FLOAT = 3) assembling the RIFF by hand,
    because the `wave` module only knows how to write PCM.

    The STANDARD shape of a float WAV is generated, which is the one ffmpeg and any DAW
    produce: an 18-byte fmt chunk (with cbSize=0) + a `fact` chunk. A reader that claims
    to support float32 has to walk the chunks, not assume a 16-byte fmt.
    """
    arr = array.array("f", xs)
    if sys.byteorder == "big":
        arr.byteswap()
    data = arr.tobytes()
    n = len(xs)

    fmt = struct.pack("<HHIIHHH", 3, 1, RATE, RATE * 4, 4, 32, 0)  # 18 bytes
    parts = [
        b"WAVE",
        b"fmt ", struct.pack("<I", len(fmt)), fmt,
        b"fact", struct.pack("<I", 4), struct.pack("<I", n),
        b"data", struct.pack("<I", len(data)), data,
    ]
    body = b"".join(parts)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", len(body)))
        f.write(body)


# ---------------------------------------------------------------------------
# The signals
# ---------------------------------------------------------------------------

AMP_20DBFS = 10.0 ** (-20.0 / 20.0)     # exactly -20 dBFS = 0.1 linear


def gen_sine(freq, dur_s=3.0):
    return _sine(freq, dur_s, AMP_20DBFS)


def gen_white_noise(dur_s=5.0):
    """Uniform white noise in [-0.25, 0.25] with a fixed seed."""
    rnd = random.Random(SEED_NOISE)
    n = _n_samples(dur_s)
    return [rnd.uniform(-0.25, 0.25) for _ in range(n)]


def gen_silence(dur_s=2.0):
    return [0.0] * _n_samples(dur_s)


def gen_clicks_bpm(bpm=132.0, dur_s=30.0, amp=0.9):
    """
    Click train at an exact BPM. Returns (signal, theoretical_times).

    The interval 60/132 s = 20045.4545 samples is not an integer: the clicks are placed
    at round(k * interval), i.e. with a maximum jitter of 0.5 sample (11 µs). That is
    negligible against the ±2 BPM tolerance and it avoids accumulating drift.
    """
    rnd = random.Random(SEED_CLICK)
    n = _n_samples(dur_s)
    xs = [0.0] * n
    step = 60.0 / bpm * RATE
    times = []
    k = 0
    while True:
        pos = int(round(k * step))
        if pos >= n:
            break
        _mix(xs, _click(amp, 5.0, 1.2, rnd), pos)
        times.append(pos / RATE)
        k += 1
    return xs, times


def gen_two_clicks(sep_ms=100.0, dur_s=0.5, amp=0.9, t0_s=0.05):
    """Two clicks spaced exactly `sep_ms` apart. The first one does NOT sit on sample 0
    (starting at 0 hands a detector an easy excuse to count only one)."""
    rnd = random.Random(SEED_CLICK)
    n = _n_samples(dur_s)
    xs = [0.0] * n
    p0 = int(round(t0_s * RATE))
    p1 = p0 + int(round(sep_ms * RATE / 1000.0))
    _mix(xs, _click(amp, 5.0, 1.2, rnd), p0)
    _mix(xs, _click(amp, 5.0, 1.2, rnd), p1)
    return xs, [p0 / RATE, p1 / RATE]


def gen_fx_roto(dur_s=0.5):
    """
    BROKEN impact: only the 45 Hz thud with decay. No body, no bite.
    It emulates the real control case "98.5 % of the energy in sub". 45 Hz falls inside
    the sub band (20-60) and the Hann window does not spill it outside: the result is
    ~98-100 % sub. (The name mirrors the file it produces, fx_roto.wav.)
    """
    xs = _sine(45.0, dur_s, 1.0)
    _decay_exp(xs, 0.10)
    _attack(xs, 2.0)
    return _normalize_peak(xs, 0.9)


def gen_fx_bueno(dur_s=0.5):
    """
    GOOD impact: the three layers fx-impact asks for. (The name mirrors the file it
    produces, fx_bueno.wav.)
      - thud   45 Hz                        -> sub
      - body   800 + 1300 Hz                -> 120-2000 Hz
      - crack  noise emphasised around 5 kHz -> >= 2000 Hz

    The three layers decay with SIMILAR time constants (80/70/50 ms) on purpose: that
    way the spectral balance is almost the same in every frame of the analysis and the
    result does not depend on whether fft_bands averages power or averages normalized
    frames. A very short crack layer would make the per-frame average "discover" an
    impact with no bite, which is exactly the false positive to avoid.
    """
    n = _n_samples(dur_s)
    xs = [0.0] * n

    thud = _sine(45.0, dur_s, FXB_GAIN_THUD)
    _decay_exp(thud, 0.080)
    _mix(xs, thud)

    body = _sine(800.0, dur_s, FXB_GAIN_BODY)
    body2 = _sine(1300.0, dur_s, FXB_GAIN_BODY * 0.5)
    for i in range(n):
        body[i] += body2[i]
    _decay_exp(body, 0.070)
    _mix(xs, body)

    rnd = random.Random(SEED_CRACK)
    noise = [rnd.uniform(-1.0, 1.0) for _ in range(n)]
    emphasis = _bandpass_biquad(list(noise), 5000.0, 1.1)
    crack = [FXB_GAIN_CRACK * (0.75 * emphasis[i] + 0.25 * noise[i]) for i in range(n)]
    _decay_exp(crack, 0.050)
    _mix(xs, crack)

    _attack(xs, 1.5)
    return _normalize_peak(xs, 0.9)


def gen_fx_swell(dur_s=0.5):
    """
    IMPACT THAT IS NOT AN IMPACT: the same three layers as the healthy one, but the
    energy swells towards the middle of the file instead of hitting at the start.
    (It produces fx_swell.wav.)

    It exists for the comparison: measured against fx_roto.wav, three metrics get
    dramatically better (sub, body and bite all come back into range) while a fourth
    one, the attack, breaks. That is the situation the whole feature was built for — a
    round of fixes that quietly costs you a metric nobody was looking at — and a test
    for it needs a signal where the directions genuinely disagree.

    The layers keep the gains of the healthy impact so the spectral balance stays
    comfortably inside the fx-impact references; the only thing that changes is WHEN
    the sound happens. The envelope is a raised cosine peaking at half the duration,
    so the peak lands around 0.5 of the file against a threshold of 0.15.
    """
    n = _n_samples(dur_s)
    xs = [0.0] * n

    _mix(xs, _sine(45.0, dur_s, FXB_GAIN_THUD))

    body = _sine(800.0, dur_s, FXB_GAIN_BODY)
    body2 = _sine(1300.0, dur_s, FXB_GAIN_BODY * 0.5)
    for i in range(n):
        body[i] += body2[i]
    _mix(xs, body)

    rnd = random.Random(SEED_CRACK)
    noise = [rnd.uniform(-1.0, 1.0) for _ in range(n)]
    emphasis = _bandpass_biquad(list(noise), 5000.0, 1.1)
    for i in range(n):
        xs[i] += FXB_GAIN_CRACK * (0.75 * emphasis[i] + 0.25 * noise[i])

    # Raised cosine over the whole file: zero at both ends, maximum exactly at the
    # middle. No decay anywhere, so the envelope peak cannot land early.
    for i in range(n):
        xs[i] *= 0.5 - 0.5 * math.cos(2.0 * math.pi * i / n)
    return _normalize_peak(xs, 0.9)


# ---------------------------------------------------------------------------
# Declared truth of each signal (what the harness takes as given)
# ---------------------------------------------------------------------------
#   peak_db  = expected peak in dBFS when reading the WAV back (v/32768 convention)
#   tol_db   = peak tolerance (covers the rounding to a 16-bit integer)
#   truth    = which property this signal has to hold up for the harness

TRUTHS = [
    {"file": "sine_50hz.wav", "dur": 3.0, "peak_db": -20.0, "tol_db": 0.10,
     "truth": "50 Hz sine, -20 dBFS -> sub band (20-60 Hz) >= 90 %"},
    {"file": "sine_1khz.wav", "dur": 3.0, "peak_db": -20.0, "tol_db": 0.10,
     "truth": "1 kHz sine, -20 dBFS -> mids >= 90 %; peak -20 dB, rms -23.01 dB"},
    {"file": "sine_10khz.wav", "dur": 3.0, "peak_db": -20.0, "tol_db": 0.10,
     "truth": "10 kHz sine, -20 dBFS -> 10 kHz falls in air (8000-16000) >= 85 %"},
    {"file": "white_noise.wav", "dur": 5.0, "peak_db": -12.04, "tol_db": 0.15,
     "truth": "uniform white noise +-0.25 (fixed seed) -> per-band energy "
              "proportional to the bandwidth"},
    {"file": "silence.wav", "dur": 2.0, "peak_db": None, "tol_db": None,
     "truth": "2 s of exact zeros -> peak 0.0; no onsets; the report does NOT say there is audio"},
    {"file": "clicks_132bpm.wav", "dur": 30.0, "peak_db": -0.915, "tol_db": 0.10,
     "truth": "66 clicks at exactly 132.000 BPM (interval 60/132 s) -> bpm 132 +-2"},
    {"file": "two_clicks_100ms.wav", "dur": 0.5, "peak_db": -0.915, "tol_db": 0.10,
     "truth": "2 clicks spaced exactly 100.00 ms apart -> onsets count == 2"},
    {"file": "fx_roto.wav", "dur": 0.5, "peak_db": -0.915, "tol_db": 0.10,
     "truth": "broken impact: only a 45 Hz thud with decay -> ~98 % in sub, no body and no crack"},
    {"file": "fx_bueno.wav", "dur": 0.5, "peak_db": -0.915, "tol_db": 0.10,
     "truth": "healthy impact: thud + body 800/1300 Hz + crack ~5 kHz, fast attack"},
    {"file": "sine_1khz_float32.wav", "dur": 3.0, "peak_db": -20.0, "tol_db": 0.10,
     "truth": "IEEE float32: same waveform as sine_1khz.wav (same integers/32768)"},
    {"file": "fx_swell.wav", "dur": 0.5, "peak_db": -0.915, "tol_db": 0.10,
     "truth": "swell, not an impact: the layers of the healthy one with the energy "
              "peaking at the middle -> spectral balance in range, attack FLAG"},
]

TRUTH_BY_FILE = {v["file"]: v for v in TRUTHS}


def generate_all():
    """Generates and writes the 11 signals. Returns a list of lines for the log."""
    os.makedirs(SIG_DIR, exist_ok=True)
    log = []

    def w16(name, xs):
        p = os.path.join(SIG_DIR, name)
        ints = write_pcm16(p, xs)
        log.append("  written %-24s %7d frames" % (name, len(ints)))
        return ints

    w16("sine_50hz.wav", gen_sine(50.0))
    ints_1k = w16("sine_1khz.wav", gen_sine(1000.0))
    w16("sine_10khz.wav", gen_sine(10000.0))
    w16("white_noise.wav", gen_white_noise())
    w16("silence.wav", gen_silence())

    clicks, t_clicks = gen_clicks_bpm()
    w16("clicks_132bpm.wav", clicks)
    log.append("     -> %d clicks, first at %.6f s, last at %.6f s"
               % (len(t_clicks), t_clicks[0], t_clicks[-1]))

    two, t_two = gen_two_clicks()
    w16("two_clicks_100ms.wav", two)
    log.append("     -> clicks at %.6f s and %.6f s (spacing %.3f ms)"
               % (t_two[0], t_two[1], (t_two[1] - t_two[0]) * 1000.0))

    w16("fx_roto.wav", gen_fx_roto())
    w16("fx_bueno.wav", gen_fx_bueno())
    w16("fx_swell.wav", gen_fx_swell())

    # The float32 is derived from the SAME integers as the PCM16: that way the comparison
    # "float32 vs PCM16 gives the same numbers" tests the reading branch and nothing else.
    p = os.path.join(SIG_DIR, "sine_1khz_float32.wav")
    write_float32(p, [i / 32768.0 for i in ints_1k])
    log.append("  written %-24s %7d frames (IEEE float32, fmt=18 + fact)"
               % ("sine_1khz_float32.wav", len(ints_1k)))
    return log


# ---------------------------------------------------------------------------
# Step 4: INDEPENDENT validation (stdlib `wave`, without touching aisinestes)
# ---------------------------------------------------------------------------

def read_with_wave(path):
    """Re-reads a PCM WAV with the stdlib `wave` module. No aisinestes here."""
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        nframes = w.getnframes()
        raw = w.readframes(nframes)
    if width != 2:
        raise ValueError("PCM16 was expected, got sampwidth=%d" % width)
    arr = array.array("h")
    arr.frombytes(raw)
    if sys.byteorder == "big":
        arr.byteswap()
    xs = [v / 32768.0 for v in arr]
    return {"rate": rate, "channels": channels, "bits": width * 8, "format": "PCM16",
            "nframes": nframes, "duration": nframes / float(rate),
            "samples": xs, "peak": max((abs(v) for v in xs), default=0.0)}


def read_riff_float(path):
    """
    Minimal RIFF parser for the float32 WAV (the `wave` module does not read format 3).
    It walks the chunks; it assumes neither sizes nor order. Independent of aisinestes.
    """
    with open(path, "rb") as f:
        header = f.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ValueError("not a RIFF/WAVE")
        fmt = None
        data = None
        while True:
            enc = f.read(8)
            if len(enc) < 8:
                break
            cid, size = struct.unpack("<4sI", enc)
            body = f.read(size)
            if size % 2 == 1:
                f.read(1)                    # padding of an odd-sized chunk
            if cid == b"fmt ":
                fmt = struct.unpack("<HHIIHH", body[:16])
            elif cid == b"data":
                data = body
        if fmt is None or data is None:
            raise ValueError("the fmt/data chunks are missing")
    code, channels, rate, _byterate, _align, bits = fmt
    if code != 3 or bits != 32:
        raise ValueError("IEEE float32 was expected (fmt=3, bits=32); got fmt=%d bits=%d"
                         % (code, bits))
    arr = array.array("f")
    arr.frombytes(data)
    if sys.byteorder == "big":
        arr.byteswap()
    xs = list(arr)
    nframes = len(xs) // channels
    return {"rate": rate, "channels": channels, "bits": bits, "format": "IEEE float32",
            "nframes": nframes, "duration": nframes / float(rate),
            "samples": xs, "peak": max((abs(v) for v in xs), default=0.0)}


def read_independent(path):
    """Picks the reader according to the format, always on pure stdlib."""
    if path.endswith("float32.wav"):
        return read_riff_float(path)
    return read_with_wave(path)


def _db(x):
    return -math.inf if x <= 0.0 else 20.0 * math.log10(x)


def validate():
    """
    Re-reads every generated WAV and compares it against its declared truth.
    Returns (rows, errors). Prints the table of step 4.
    """
    rows = []
    errors = []
    for v in TRUTHS:
        name = v["file"]
        path = os.path.join(SIG_DIR, name)
        if not os.path.exists(path):
            errors.append("%s: DOES NOT EXIST" % name)
            rows.append((name, "-", "-", "-", "-", "MISSING"))
            continue
        try:
            d = read_independent(path)
        except Exception as e:
            errors.append("%s: could not be re-read -> %s: %s" % (name, type(e).__name__, e))
            rows.append((name, "-", "-", "-", "-", "ERROR"))
            continue

        problems = []
        if d["rate"] != RATE:
            problems.append("rate %d != %d" % (d["rate"], RATE))
        if d["channels"] != 1:
            problems.append("channels %d != 1" % d["channels"])

        expected_frames = _n_samples(v["dur"])
        if d["nframes"] != expected_frames:
            problems.append("frames %d != %d" % (d["nframes"], expected_frames))
        if abs(d["duration"] - v["dur"]) > 1e-6:
            problems.append("duration %.6f != %.6f" % (d["duration"], v["dur"]))

        peak_db = _db(d["peak"])
        if v["peak_db"] is None:
            # silence: the peak has to be exactly ZERO, not "almost zero"
            if d["peak"] != 0.0:
                problems.append("peak %.9f != exactly 0.0" % d["peak"])
            peak_text = "0.0 (-inf dB)"
        else:
            if abs(peak_db - v["peak_db"]) > v["tol_db"]:
                problems.append("peak %.4f dB outside %.3f +-%.2f"
                                % (peak_db, v["peak_db"], v["tol_db"]))
            peak_text = "%.4f dB" % peak_db

        status = "OK" if not problems else "BAD"
        if problems:
            errors.append("%s: %s" % (name, "; ".join(problems)))
        rows.append((name, d["format"], "%d" % d["rate"], "%d" % d["nframes"],
                     "%.6f" % d["duration"], peak_text, status))

    return rows, errors


# ---------------------------------------------------------------------------
# INDEPENDENT spectral measurement (own FFT, not the one in analyze.py)
# ---------------------------------------------------------------------------

def _fft(x):
    """Iterative Cooley-Tukey, radix-2, in-place. `x` is a list of complex numbers."""
    n = len(x)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            x[i], x[j] = x[j], x[i]
    length = 2
    while length <= n:
        wl = cmath.exp(-2j * math.pi / length)
        half = length >> 1
        for i in range(0, n, length):
            w = 1 + 0j
            for k in range(i, i + half):
                u = x[k]
                v = x[k + half] * w
                x[k] = u + v
                x[k + half] = u - v
                w *= wl
        length <<= 1
    return x


def independent_bands(samples, rate, n_fft=8192, hop=4096, max_frames=12):
    """
    Frame-averaged spectrum (Hann n_fft, hop) split across the CONTRACT bands, computed
    right here. It is the generator's cross-check: it says what spectral balance the
    signals REALLY have, without asking analyze.py anything.
    """
    n = len(samples)
    if n < n_fft:
        return None
    starts = list(range(0, n - n_fft + 1, hop))
    if len(starts) > max_frames:
        step = len(starts) / float(max_frames)
        starts = [starts[int(i * step)] for i in range(max_frames)]
    hann = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / n_fft) for i in range(n_fft)]

    accum = [0.0] * (n_fft // 2 + 1)
    for a in starts:
        buf = [complex(samples[a + i] * hann[i], 0.0) for i in range(n_fft)]
        _fft(buf)
        for k in range(len(accum)):
            accum[k] += abs(buf[k]) ** 2

    total_bands = 0.0
    per_band = {}
    for name, lo, hi in CONTRACT_BANDS:
        s = 0.0
        for k in range(1, len(accum)):
            f = k * rate / float(n_fft)
            if lo <= f < hi:
                s += accum[k]
        per_band[name] = s
        total_bands += s
    if total_bands <= 0.0:
        return {n: 0.0 for n, _l, _h in CONTRACT_BANDS}
    return {k: 100.0 * v / total_bands for k, v in per_band.items()}


def measure_spectra():
    """Table of the real spectral balance of the signals that depend on it."""
    of_interest = ["sine_50hz.wav", "sine_1khz.wav", "sine_10khz.wav", "white_noise.wav",
                   "fx_roto.wav", "fx_bueno.wav", "fx_swell.wav"]
    rows = []
    for name in of_interest:
        path = os.path.join(SIG_DIR, name)
        if not os.path.exists(path):
            continue
        d = read_independent(path)
        b = independent_bands(d["samples"], d["rate"])
        if b is None:
            continue
        rows.append((name, b))
    return rows


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    only_validate = "--only-validate" in argv
    no_spectrum = "--no-spectrum" in argv

    print("=" * 78)
    print("make_signals.py — synthetic signals with known truth (Aisinestes)")
    print("=" * 78)
    print("destination: %s" % SIG_DIR)
    print("")

    if not only_validate:
        print("[1] Generating...")
        for line in generate_all():
            print(line)
        print("")

    print("[2] INDEPENDENT VALIDATION (stdlib `wave` module, without aisinestes)")
    rows, errors = validate()
    header = ("file", "format", "rate", "frames", "dur (s)", "peak", "")
    print("  %-24s %-12s %-6s %-8s %-10s %-14s %s" % header)
    print("  " + "-" * 84)
    for f in rows:
        if len(f) == 6:
            f = f + ("",)
        print("  %-24s %-12s %-6s %-8s %-10s %-14s %s" % f)
    print("")

    if errors:
        print("  GENERATOR DEFECTS (%d):" % len(errors))
        for e in errors:
            print("    - %s" % e)
    else:
        print("  Every signal holds its declared truth (rate/frames/duration/peak).")
    print("")

    if not no_spectrum:
        print("[3] MEASURED SPECTRAL BALANCE (the harness's own FFT, not analyze.py's)")
        names = [n for n, _l, _h in CONTRACT_BANDS]
        print("  %-20s %s" % ("file", " ".join("%13s" % n for n in names)))
        print("  " + "-" * 100)
        for name, b in measure_spectra():
            print("  %-20s %s" % (name, " ".join("%12.2f%%" % b[n] for n in names)))
        print("")

    print("[4] DECLARED TRUTH PER SIGNAL")
    for v in TRUTHS:
        print("  %-24s %s" % (v["file"], v["truth"]))
    print("")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
