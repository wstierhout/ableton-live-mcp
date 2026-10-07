"""Device parameter values: numbers sent as text, and -inf dB displays (no Live needed).

The fake parameters are modelled on Live 11.3.43 readings: Utility Gain (native -1..1,
"-inf dB" at -1, "35.0 dB" at 1), Utility Channel Mode, Auto Filter Frequency, and
Auto Filter LFO Quantize Rate, whose enum labels are themselves numbers.
"""

import ast
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = (Path(__file__).parents[1] / "ableton_live_mcp/remote_script/__init__.py").read_text()
HELPERS = ast.Module(
    body=[
        n
        for n in ast.parse(SOURCE).body
        if isinstance(n, ast.FunctionDef) and n.name in {"_display_number", "_parameter_value"}
    ],
    type_ignores=[],
)
NAMESPACE = {"math": math, "re": re}
exec(compile(HELPERS, "parameter_helpers", "exec"), NAMESPACE)
resolve = NAMESPACE["_parameter_value"]


def _gain(minus="-", infinity="inf"):
    return SimpleNamespace(
        min=-1.0,
        max=1.0,
        is_quantized=False,
        str_for_value=lambda x: f"{minus}{infinity} dB" if x <= -1 else f"{35 * x:.1f} dB",
    )


def _enum(labels):
    return SimpleNamespace(
        min=0.0,
        max=float(len(labels) - 1),
        is_quantized=True,
        str_for_value=lambda v: labels[int(v)],
    )


FREQUENCY = SimpleNamespace(
    min=20.0,
    max=135.0,
    is_quantized=False,
    str_for_value=lambda x: f"{20 * 1.06 ** (x - 20):.1f} Hz",
)
CHANNEL_MODE = _enum(["Left", "Stereo", "Right", "Swap"])
LFO_QUANTIZE_RATE = _enum(["0.5", "1", "2", "3", "4", "5", "6", "8", "12", "16"])


def test_numbers_sent_as_text_are_native_numbers():
    """Live 11.3.43: set_device_parameter(value=-0.5) arrived as "-0.5" and was rejected
    as display text, while the same number inside batch_commands worked."""
    assert resolve(_gain(), "-0.5") == resolve(_gain(), -0.5) == (-0.5, None)
    assert resolve(FREQUENCY, "40") == (40.0, None)
    assert resolve(FREQUENCY, " 1e2 ") == (100.0, None)
    assert resolve(FREQUENCY, "200") == (135.0, "clamped or quantized to native range")


def test_numeric_text_on_an_enum_without_that_label_is_a_native_index():
    assert resolve(CHANNEL_MODE, "2") == (2, None)
    assert resolve(CHANNEL_MODE, "2.6") == (3, "clamped or quantized to native range")
    assert resolve(CHANNEL_MODE, "stereo") == (1, None)  # labels still work


def test_a_number_like_label_still_wins_and_says_so():
    value, warning = resolve(LFO_QUANTIZE_RATE, "8")
    assert value == 7 and "label '8' (native 7)" in warning
    assert resolve(LFO_QUANTIZE_RATE, "3") == (3, None)  # label and index agree
    assert resolve(LFO_QUANTIZE_RATE, "7") == (7, None)  # no such label: native index
    assert resolve(LFO_QUANTIZE_RATE, 8) == (8, None)  # a real number is always native


def test_negative_db_resolves_when_the_minimum_displays_minus_inf():
    """Live 11.3.43: Utility Gain "-6 dB" failed because sampling the range hit "-inf dB"."""
    raw, warning = resolve(_gain(), "-6 dB")
    assert 35 * raw == pytest.approx(-6, abs=0.06) and "approximate" in warning
    raw, _ = resolve(_gain(), "-34 dB")  # between the -inf sample and the next one
    assert 35 * raw == pytest.approx(-34, abs=0.2)


@pytest.mark.parametrize("gain", [_gain(), _gain(minus="\u2212", infinity="\u221e")])
def test_minus_inf_selects_the_end_of_the_range_that_displays_it(gain):
    assert resolve(gain, "-inf dB") == (-1.0, None)
    assert resolve(gain, "\u2212\u221e dB") == (-1.0, None)
    raw, _ = resolve(gain, "-6 dB")
    assert 35 * raw == pytest.approx(-6, abs=0.06)


@pytest.mark.parametrize(
    "param, value, message",
    [
        (_gain(), "inf dB", "Neither end"),
        (FREQUENCY, "-inf Hz", "Neither end"),
        (_gain(), float("-inf"), "finite"),
        (_gain(), "-inf", "display text"),
        (_gain(), "-40 Hz", "unit does not match"),
        (CHANNEL_MODE, "Mono", "missing or ambiguous"),
    ],
)
def test_still_rejected(param, value, message):
    with pytest.raises(ValueError, match=message):
        resolve(param, value)
