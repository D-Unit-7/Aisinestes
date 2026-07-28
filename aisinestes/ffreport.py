"""Aisinestes ffmpeg layer: EBU R128 loudness + images (spectrogram and waveform).

Anything that needs to really measure perceived level (LUFS/LRA/true peak) or draw an
image is delegated to ffmpeg, which is already on the machine. This module does NO DSP
of its own: it only builds the command line, runs the process with a timeout and parses
the output.

Hard rules honoured here:
  - pure stdlib, zero network, zero downloads.
  - every ffmpeg call goes through _ffmpeg(), with a timeout and a guaranteed process kill.
  - if the parser cannot find the block it expects, it BLOWS UP with a clear message.
    A made-up number or a filler 0 is never returned.
"""

import math
import os
import re
import shutil
import subprocess

# ffmpeg is OPTIONAL: without it you lose loudness (EBU R128) and the images, and everything
# else keeps working. Nothing is ever downloaded or installed.
#
# Search order, from highest to lowest priority:
#   1. the AISINESTES_FFMPEG environment variable, if it points to an executable
#   2. the ffmpeg.local file (a single line with the path), which is NOT committed to the repo
#   3. the system PATH — the norm on Linux/macOS and on any decent Windows install
#   4. standard locations of an ffmpeg installation
#
# ⚠️ We deliberately do NOT point at the ffmpeg bundled inside other programs. That binary
# ships with the licence and the purpose of THAT program; using it from here would not be
# right, and besides, nobody else has it. Whoever needs loudness or images installs ffmpeg.
#
# The options used below were verified against ffmpeg 4.4.1; newer versions accept
# everything used here.
FFMPEG_ENV_VAR = "AISINESTES_FFMPEG"
LOCAL_FILE = "ffmpeg.local"


def _from_local_file():
    """Reads the path from an `ffmpeg.local` at the project root, if it exists.

    It is the escape hatch for machines where ffmpeg is not on the PATH, without polluting
    the code with anybody's path: the file is in .gitignore and is never published.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, LOCAL_FILE)
    try:
        # utf-8-sig and not utf-8: several Windows editors write the file with a BOM, and those
        # three invisible bytes at the start made the comment line NOT begin with '#', so it was
        # taken as if it were the path. With -sig the BOM is discarded on its own.
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip().strip('"')
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return None


def _known_paths():
    """Standard locations of an ffmpeg installation, built from system variables —
    never from a specific user's folder."""
    paths = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            paths.append(os.path.join(base, "ffmpeg", "bin", "ffmpeg.exe"))
    paths.extend([
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/snap/bin/ffmpeg",
    ])
    return paths

# Time ceiling for any ffmpeg invocation. The contract requires <= 60 s.
TIMEOUT_S = 60

# "Readable" image sizes. The spectrogram comes out larger than this because ffmpeg
# adds the legend margins around the data area.
SPECTROGRAM_SIZE = "1600x800"
WAVEFORM_SIZE = "1600x400"


def _find_ffmpeg():
    """Returns the path to the ffmpeg executable or blows up with a clear message."""
    proposed = os.environ.get(FFMPEG_ENV_VAR)
    if proposed and os.path.isfile(proposed):
        return proposed
    local = _from_local_file()
    if local and os.path.isfile(local):
        return local
    found = shutil.which("ffmpeg")
    if found:
        return found
    for path in _known_paths():
        if os.path.isfile(path):
            return path
    raise RuntimeError(
        "ffmpeg was not found. Looked in the %s variable, in the %s file, on the PATH and in "
        "the standard locations. Nothing is downloaded or installed: install ffmpeg, or put its "
        "path in %s (a single line), or point %s at the binary."
        % (FFMPEG_ENV_VAR, LOCAL_FILE, LOCAL_FILE, FFMPEG_ENV_VAR)
    )


def _ffmpeg(args, timeout=TIMEOUT_S):
    """Runs ffmpeg with the given arguments and returns its stderr as text.

    ffmpeg writes ALL of its diagnostic information (including the ebur128 summary) to
    stderr, not to stdout, so that is what we care about.

    "Zero orphans" guarantee: subprocess.run kills the child process both when the timeout
    expires and when any other exception is raised (it does so in its own try/except block,
    and Popen's context manager waits for the child on exit). On top of that, -nostdin and
    stdin=DEVNULL are passed so that ffmpeg cannot block waiting for keyboard input.
    """
    executable = _find_ffmpeg()
    command = [executable, "-hide_banner", "-nostdin", "-y"] + list(args)
    try:
        proc = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run already killed the child before propagating; here we only translate
        # the error.
        raise RuntimeError(
            "ffmpeg exceeded the %d s limit and was terminated. Command: %s"
            % (timeout, " ".join(command))
        )
    err_output = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg exited with code %d.\nCommand: %s\nLast lines from ffmpeg:\n%s"
            % (proc.returncode, " ".join(command), _tail(err_output))
        )
    return err_output


def _tail(text, n=1200):
    """Last n characters of a text, for bounded but useful error messages."""
    text = text.strip()
    if len(text) <= n:
        return text
    return "...\n" + text[-n:]


def _ensure_folder(file_path):
    """Creates the containing folder of the output file if needed."""
    folder = os.path.dirname(os.path.abspath(file_path))
    if folder:
        os.makedirs(folder, exist_ok=True)


def _validate_input(path):
    """Fails early and clearly if the input audio does not exist."""
    if not os.path.isfile(path):
        raise FileNotFoundError("The audio file does not exist: %s" % path)


# A possibly negative number, with decimals, or the literals ffmpeg uses for absolute
# silence / undefined ("-inf", "nan"). A decimal comma or dot is accepted.
_NUM = r"(-?(?:inf|nan|\d+(?:[.,]\d+)?))"


def _to_float(text):
    """Converts the captured token to float, tolerating a decimal comma, inf and nan."""
    clean = text.strip().replace(",", ".").lower()
    if clean in ("inf", "+inf"):
        return math.inf
    if clean == "-inf":
        return -math.inf
    if clean == "nan":
        return math.nan
    return float(clean)


def loudness(path):
    """Measures EBU R128 loudness with ffmpeg and returns the three numbers of the summary.

    Returns: {"lufs_i": float, "lra": float, "true_peak_db": float}

    The ebur128 filter is used with peak=true (true-peak mode, with oversampling) and the
    "Summary:" block that ffmpeg prints to stderr when it finishes the file is parsed.
    framelog=verbose silences the per-frame log (one line every 100 ms) without touching
    the summary, which is emitted at info level: less stderr to parse, same result.
    """
    _validate_input(path)
    err_output = _ffmpeg([
        "-i", path,
        "-af", "ebur128=peak=true:framelog=verbose",
        "-f", "null", "-",
    ])
    return _parse_ebur128_summary(err_output, path)


def _parse_ebur128_summary(err_output, path):
    """Extracts I / LRA / true peak from the ebur128 Summary block.

    The block emitted by ffmpeg 4.4.1 has this shape (the spacing varies with the width
    of the numbers, which is why the parser uses \\s* instead of fixed positions):

        [Parsed_ebur128_0 @ ...] Summary:

          Integrated loudness:
            I:         -22.7 LUFS
            Threshold: -32.7 LUFS

          Loudness range:
            LRA:         0.8 LU
            ...
          True peak:
            Peak:       -1.7 dBFS
    """
    cut = err_output.rfind("Summary:")
    if cut == -1:
        raise RuntimeError(
            "ebur128 did not emit the 'Summary:' block for '%s'. Values are never made up.\n"
            "ffmpeg output:\n%s" % (path, _tail(err_output))
        )
    block = err_output[cut:]

    # \bI: does not collide with 'LRA low:'/'LRA high:'/'Threshold:' because in those cases
    # the colon is attached to another word.
    m_i = re.search(r"\bI:\s*" + _NUM + r"\s*LUFS", block)
    m_lra = re.search(r"\bLRA:\s*" + _NUM + r"\s*LU\b", block)
    # The true peak lives inside the 'True peak:' section, on a 'Peak: ... dBFS' line.
    m_tp = re.search(r"True peak:\s*.*?\bPeak:\s*" + _NUM + r"\s*dBFS", block, re.S)

    missing = []
    if m_i is None:
        missing.append("I (integrated LUFS)")
    if m_lra is None:
        missing.append("LRA (loudness range)")
    if m_tp is None:
        missing.append("True peak (dBFS)")
    if missing:
        raise RuntimeError(
            "The ebur128 Summary block showed up but could not be read: %s. "
            "It may be a format change in this ffmpeg build. Values are never made up.\n"
            "Block received:\n%s" % (", ".join(missing), _tail(block))
        )

    return {
        "lufs_i": _to_float(m_i.group(1)),
        "lra": _to_float(m_lra.group(1)),
        "true_peak_db": _to_float(m_tp.group(1)),
    }


def spectrogram(path, out_png):
    """Writes a readable PNG spectrogram of the audio file.

    Options chosen and verified against ffmpeg 4.4.1:
      - fscale=log  -> logarithmic frequency axis. In 4.4.1 the default is lin, which
                       squashes the whole low end; with log the sub and the bass (where
                       the "broken impact" problem is decided) get real estate.
      - legend=1    -> time/frequency axes and dB bar. Without the legend the image is
                       useless for reading numbers, only good for looking at blobs.
      - scale=log   -> amplitude in dB (it is this version's default, kept explicit).
      - gain=1      -> NO boost. Raising the gain brightens the image but misaligns the
                       colours with respect to the legend's dB scale: the legend would
                       start lying. A faithful image is preferred.
      - color=intensity -> dark->light palette by energy, the easiest one to read.
    """
    _validate_input(path)
    _ensure_folder(out_png)
    filter_str = (
        "showspectrumpic=s=%s:legend=1:fscale=log:scale=log:gain=1:color=intensity"
        % SPECTROGRAM_SIZE
    )
    _ffmpeg(["-i", path, "-lavfi", filter_str, "-frames:v", "1", out_png])


def waveform(path, out_png):
    """Writes a PNG with the full waveform of the file.

    showwavespic delivers RGBA with a transparent background; depending on the viewer that
    shows up as white, black or a checkerboard. format=rgb24 is chained to flatten the
    transparency to black so the image looks the same everywhere.
    """
    _validate_input(path)
    _ensure_folder(out_png)
    filter_str = (
        "showwavespic=s=%s:colors=0x33ddff:split_channels=0,format=rgb24"
        % WAVEFORM_SIZE
    )
    _ffmpeg(["-i", path, "-lavfi", filter_str, "-frames:v", "1", out_png])
