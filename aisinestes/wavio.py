"""WAV file reading on pure stdlib (zero dependencies, zero network).

Supports 16/24/32-bit integer PCM and 32-bit IEEE float, mono or multichannel.
Any other format (8-bit, a-law, mu-law, compressed, etc.) raises ValueError with a
clear message.

Strategy: the stdlib `wave` module is tried first (fast and battle-tested) and, if it
does not understand the file (float32, WAVE_FORMAT_EXTENSIBLE, odd chunks), it falls
back to an own RIFF parser. The conversion to mono is done with C-level operations
(array slicing + map with built-in operators) so that a 3-minute WAV is read in a
couple of seconds instead of a minute.
"""

import array
import operator
import os
import struct
import sys
import wave

# Format codes of the 'fmt ' chunk of a RIFF/WAVE.
_FMT_PCM = 0x0001
_FMT_IEEE_FLOAT = 0x0003
_FMT_EXTENSIBLE = 0xFFFE

# Array typecode for 32-bit integers (on CPython 'i' is always 4 bytes wide,
# but it is checked just in case instead of assumed).
_TC_INT32 = "i" if array.array("i").itemsize == 4 else "l"

# Normalization scales to [-1, 1] according to the PCM bit depth.
_SCALE_16 = 1.0 / 32768.0
_SCALE_32 = 1.0 / 2147483648.0


def read(path: str) -> dict:
    """Reads a WAV and returns the contract dictionary.

    {"rate": int, "channels": int, "bits": int,
     "samples": mono array.array('d') in [-1.0, 1.0],
     "duration": float, "peak": float}
    """
    if not isinstance(path, str) or not path:
        raise ValueError("wavio.read: a valid file path must be provided")
    if not os.path.isfile(path):
        raise FileNotFoundError("wavio.read: file %s does not exist" % path)

    info = _read_raw(path)
    channels = info["channels"]
    bits = info["bits"]
    rate = info["rate"]

    if channels < 1:
        raise ValueError("wavio.read: invalid channel count (%d)" % channels)
    if rate < 1:
        raise ValueError("wavio.read: invalid sample rate (%d)" % rate)

    samples = _to_mono(info["data"], channels, bits, info["fmt"])

    if samples:
        # max() and min() over array('d') run at C level: cheap even with millions of values.
        peak = max(max(samples), -min(samples))
    else:
        peak = 0.0

    return {
        "rate": rate,
        "channels": channels,
        "bits": bits,
        "samples": samples,
        "duration": len(samples) / float(rate),
        "peak": float(peak),
    }


def _read_raw(path: str) -> dict:
    """Returns {'rate','channels','bits','fmt','data'} using `wave` or the own parser."""
    try:
        with wave.open(path, "rb") as w:
            if w.getcomptype() != "NONE":
                raise wave.Error("compressed")
            bits = w.getsampwidth() * 8
            return {
                "rate": w.getframerate(),
                "channels": w.getnchannels(),
                "bits": bits,
                "fmt": _FMT_PCM,
                "data": w.readframes(w.getnframes()),
            }
    except (wave.Error, EOFError):
        # `wave` supports neither float32 nor WAVE_FORMAT_EXTENSIBLE: parse the RIFF by hand.
        return _parse_riff(path)


def _parse_riff(path: str) -> dict:
    """Minimal RIFF/WAVE parser: walks the chunks and keeps 'fmt ' and 'data'."""
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ValueError(
                "wavio.read: %s is not a RIFF/WAVE (mp3, ogg, flac or a corrupt file?)"
                % os.path.basename(path)
            )

        fmt_code = channels = rate = bits = None
        data = None

        while True:
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                break
            cid = chunk_header[0:4]
            csize = struct.unpack("<I", chunk_header[4:8])[0]

            if cid == b"fmt ":
                body = f.read(csize)
                if len(body) < 16:
                    raise ValueError("wavio.read: truncated 'fmt ' chunk")
                fmt_code, channels, rate, _byterate, _align, bits = struct.unpack(
                    "<HHIIHH", body[:16]
                )
                if fmt_code == _FMT_EXTENSIBLE and len(body) >= 26:
                    # On EXTENSIBLE the real format is the first 2 bytes of the SubFormat GUID.
                    fmt_code = struct.unpack("<H", body[24:26])[0]
            elif cid == b"data":
                # Some encoders write size 0 or 0xFFFFFFFF (streaming): read until EOF.
                if csize == 0 or csize == 0xFFFFFFFF:
                    data = f.read()
                else:
                    data = f.read(csize)
            else:
                f.seek(csize, 1)

            if csize & 1:
                f.seek(1, 1)  # RIFF chunks are aligned to even bytes

    if fmt_code is None:
        raise ValueError("wavio.read: the WAV has no 'fmt ' chunk")
    if data is None:
        raise ValueError("wavio.read: the WAV has no 'data' chunk")

    return {
        "rate": rate,
        "channels": channels,
        "bits": bits,
        "fmt": fmt_code,
        "data": data,
    }


def _to_mono(data: bytes, channels: int, bits: int, fmt_code: int) -> array.array:
    """Converts the raw byte block to a mono array('d') normalized to [-1, 1]."""
    if fmt_code == _FMT_IEEE_FLOAT:
        if bits != 32:
            raise ValueError(
                "wavio.read: %d-bit IEEE float not supported (float32 only)" % bits
            )
        raw = _from_bytes("f", data, 4)
        scale = 1.0
        clip = True  # the float may come in out of range
    elif fmt_code == _FMT_PCM:
        if bits == 16:
            raw = _from_bytes("h", data, 2)
            scale = _SCALE_16
        elif bits == 24:
            raw = _expand_24(data)  # ends up as int32 = value * 256
            scale = _SCALE_32
        elif bits == 32:
            raw = _from_bytes(_TC_INT32, data, 4)
            scale = _SCALE_32
        else:
            raise ValueError(
                "wavio.read: %d-bit PCM not supported (supported: 16, 24, 32)" % bits
            )
        clip = False
    else:
        raise ValueError(
            "wavio.read: WAV format 0x%04X not supported "
            "(supported: PCM 0x0001 and IEEE float32 0x0003)" % fmt_code
        )

    mono = _mix(raw, channels, scale)

    if clip and mono and (max(mono) > 1.0 or min(mono) < -1.0):
        # Hard clip: the contract demands samples inside [-1, 1].
        mono = array.array("d", (1.0 if v > 1.0 else (-1.0 if v < -1.0 else v) for v in mono))
    return mono


def _from_bytes(typecode: str, data: bytes, width: int) -> array.array:
    """array.frombytes with truncation to a multiple of the width and endianness fix."""
    a = array.array(typecode)
    usable = len(data) - (len(data) % width)
    a.frombytes(data[:usable])
    if sys.byteorder != "little":
        a.byteswap()  # WAV is always little-endian
    return a


def _expand_24(data: bytes) -> array.array:
    """Converts 24-bit PCM to int32 (value * 256) with C-level slicing.

    Every byte of the 24-bit sample is copied into the 3 high bytes of an int32 and
    the low byte is left at zero: the sign is preserved and the result is the original
    value multiplied by 256, which is compensated by using the 32-bit scale.
    """
    n = len(data) // 3
    usable = n * 3
    buf = bytearray(n * 4)
    buf[1::4] = data[0:usable:3]
    buf[2::4] = data[1:usable:3]
    buf[3::4] = data[2:usable:3]
    a = array.array(_TC_INT32)
    a.frombytes(bytes(buf))
    if sys.byteorder != "little":
        a.byteswap()
    return a


def _mix(raw: array.array, channels: int, scale: float) -> array.array:
    """Averages the interleaved channels and normalizes, all with built-in operators."""
    if channels == 1:
        return array.array("d", map(scale.__mul__, raw))

    tracks = [raw[c::channels] for c in range(channels)]
    # map(operator.add, ...) is lazy and runs at C level: it materializes no intermediates.
    total = map(operator.add, tracks[0], tracks[1])
    for extra in tracks[2:]:
        total = map(operator.add, total, extra)
    k = scale / channels
    return array.array("d", map(k.__mul__, total))
