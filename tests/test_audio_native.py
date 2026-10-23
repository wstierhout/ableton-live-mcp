"""Numeric parity of the optional C extension and its Python reference."""

import array
import math
import random

import pytest

from ableton_live_mcp import audio_stats


@pytest.mark.parametrize("width", [1, 2, 3, 4])
@pytest.mark.parametrize("channels", [1, 2, 8])
def test_integer_pcm_accumulation_exact(width, channels):
    native = pytest.importorskip("ableton_live_mcp._audio_native")
    rng = random.Random(572)
    scale = 2 ** (width * 8 - 1)
    samples = [-scale, scale - 1, 0, -1, 1] + [rng.randrange(-scale, scale) for _ in range(1027)]
    samples = (samples * channels)[: (len(samples) * channels)]
    pcm = b"".join(
        (bytes([v + 128]) if width == 1 else v.to_bytes(width, "little", signed=True))
        for v in samples
    )
    expected = actual = tuple([0.0] * channels for _ in range(4))
    frame_bytes = width * channels
    for start in range(0, len(pcm), frame_bytes * 53):
        block = pcm[start : start + frame_bytes * 53]
        expected = audio_stats.python_pcm_accumulate(block, width, channels, *expected)
        actual = native.pcm_accumulate(block, width, channels, *actual)
    assert actual == expected


@pytest.mark.parametrize(
    "values",
    [
        [0.0] * 100,
        [-1.0, 1.0, 1.5, -1.5, 0.0],
        [1e16, 1.0, -1e16, 1.0] * 1024,
        [random.Random(seed).uniform(-2, 2) for seed in range(4096)],
    ],
)
def test_decoded_compensated_sums_match_fsum(values):
    native = pytest.importorskip("ableton_live_mcp._audio_native")
    data = array.array("d", values).tobytes()
    expected = audio_stats.python_float_stats(data, len(values))
    result = native.float_stats(data, len(values))
    assert result[0] == expected[0]
    assert result[3] == expected[3]
    assert result[1:3] == pytest.approx(expected[1:3], rel=1e-14, abs=1e-14)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 1e308])
def test_decoded_invalid_samples_fail(value):
    data = array.array("d", [value]).tobytes()
    with pytest.raises((ValueError, OverflowError)):
        audio_stats.python_float_stats(data, 1)
    native = pytest.importorskip("ableton_live_mcp._audio_native")
    with pytest.raises(ValueError):
        native.float_stats(data, 1)


def test_native_checks_buffer_bounds():
    native = pytest.importorskip("ableton_live_mcp._audio_native")
    with pytest.raises(ValueError):
        native.float_stats(b"short", 2)
    with pytest.raises(ValueError):
        native.pcm_accumulate(b"x", 4, 2, *([0, 0] for _ in range(4)))
    with pytest.raises(ValueError):
        native.pcm_accumulate(b"", 1, 9, *([0] * 9 for _ in range(4)))
