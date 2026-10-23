"""Audio formats, channel metrics and native kernels with a portable fallback."""

import array
import math

FORMATS = {
    ".wav": "wav",
    ".wave": "wav",
    ".aif": "aiff",
    ".aiff": "aiff",
    ".aifc": "aiff",
    ".flac": "flac",
    ".ogg": "ogg",
    ".oga": "ogg",
    ".mp3": "mp3",
    ".aac": "aac",
    ".m4a": "mov",
    ".mp4": "mov",
}

try:
    from ._audio_native import float_stats, pcm_accumulate

    NATIVE_AUDIO = True
except ImportError:
    NATIVE_AUDIO = False


def _db(value):
    return round(20 * math.log10(value), 4) if value > 0 else None


def channel_metrics(count, peak, square, sums, full_scale):
    metrics = []
    for channel in range(len(peak)):
        rms = math.sqrt(square[channel] / count)
        metrics.append(
            {
                "channel": channel,
                "sample_peak_dbfs": _db(peak[channel]),
                "rms_dbfs": _db(rms),
                "dc_offset": sums[channel] / count,
                "full_scale_samples": int(full_scale[channel]),
                "crest_factor_db": _db(peak[channel] / rms) if rms else None,
            }
        )
    return metrics


def python_pcm_accumulate(chunk, width, channels, peak, square, sums, clipped):
    peak, square, sums, clipped = (list(values) for values in (peak, square, sums, clipped))
    scale = 2 ** (width * 8 - 1)
    for offset in range(0, len(chunk), width):
        channel = (offset // width) % channels
        integer = (
            chunk[offset] - 128
            if width == 1
            else int.from_bytes(chunk[offset : offset + width], "little", signed=True)
        )
        value = integer / scale
        peak[channel] = max(peak[channel], abs(value))
        square[channel] += value * value
        sums[channel] += value
        clipped[channel] += integer <= -scale or integer >= scale - 1
    return peak, square, sums, clipped


def python_float_stats(data, count):
    values = array.array("d")
    values.frombytes(data[: count * 8])
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Audio contains NaN or infinite samples")
    square = math.fsum(value * value for value in values)
    if not math.isfinite(square):
        raise ValueError("Audio energy overflow")
    return (
        max(map(abs, values), default=0),
        square,
        math.fsum(values),
        sum(abs(value) >= 1.0 for value in values),
    )


if not NATIVE_AUDIO:
    pcm_accumulate = python_pcm_accumulate
    float_stats = python_float_stats
