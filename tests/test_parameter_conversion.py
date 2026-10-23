"""Regression coverage adapted from PRs #6 (Tom Glass) and #7 (kovtun-oleksii).

The bridge runs without Live here. Its real Windows/Live display strings still
need acceptance checks; these fixtures exercise read-only conversion and writes.
"""

import math
from types import SimpleNamespace

import pytest


class Parameter:
    name = "Value"
    is_enabled = True

    def __init__(self, low, high, display, quantized=False):
        self.min, self.max = low, high
        self.is_quantized = quantized
        self._display = display
        self._value = low
        self.writes = []
        self.display_reads = []

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self.writes.append(value)
        self._value = value

    def str_for_value(self, value):
        self.display_reads.append(value)
        return self._display(value)


def gain(unicode=False, descending=False):
    def display(value):
        directed = -value if descending else value
        if directed <= -1:
            return "\u2212\u221e dB" if unicode else "-inf dB"
        return f"{35 * directed:.2f} dB"

    return Parameter(-1, 1, display)


def enum(labels):
    return Parameter(0, len(labels) - 1, lambda value: labels[int(value)], quantized=True)


@pytest.mark.parametrize("requested", [-0.5, "-0.5", " -.5 ", "\u2212.5", "-5e-1"])
def test_numeric_text_keeps_native_meaning_without_reading_or_writing(bridge_module, requested):
    param = gain()
    assert bridge_module._parameter_value(param, requested) == (-0.5, None)
    assert param.writes == param.display_reads == []


@pytest.mark.parametrize("requested", [2, "2", "2e0"])
def test_native_numbers_and_strings_clamp_with_structured_warning(bridge_module, requested):
    param = gain()
    assert bridge_module._parameter_value(param, requested) == (
        1.0,
        "clamped or quantized to native range",
    )
    assert param.writes == []


def test_numeric_labels_win_but_real_numbers_remain_native(bridge_module):
    param = enum(["0.5", "1", "2", "3", "4", "5", "6", "8", "12", "16"])
    native, warning = bridge_module._parameter_value(param, "8")
    assert native == 7
    assert "label '8' (native 7)" in warning
    assert bridge_module._parameter_value(param, 8) == (8, None)
    assert bridge_module._parameter_value(param, "3") == (3, None)
    assert bridge_module._parameter_value(param, "7") == (7, None)
    assert param.writes == []


def test_enum_numeric_fallback_rounds_and_clamps_with_a_warning(bridge_module):
    param = enum(["Left", "Stereo", "Right", "Swap"])
    assert bridge_module._parameter_value(param, " stereo ") == (1, None)
    assert bridge_module._parameter_value(param, "2") == (2, None)
    assert bridge_module._parameter_value(param, "2.6") == (
        3,
        "clamped or quantized to native range",
    )
    assert bridge_module._parameter_value(param, "99") == (
        3,
        "clamped or quantized to native range",
    )


@pytest.mark.parametrize("labels, requested", [(["8", "8"], "8"), (["Auto", "AUTO"], "auto")])
def test_duplicate_labels_do_not_silently_choose_a_native_index(bridge_module, labels, requested):
    param = enum(labels)
    with pytest.raises(ValueError, match="ambiguous"):
        bridge_module._parameter_value(param, requested)
    assert param.writes == []


def test_large_enums_require_a_real_native_number_without_scanning_labels(bridge_module):
    param = Parameter(0, 100000, lambda value: str(value), quantized=True)
    for requested in ("2", "Auto"):
        with pytest.raises(ValueError, match="Enum range too large"):
            bridge_module._parameter_value(param, requested)
    assert bridge_module._parameter_value(param, 2) == (2, None)
    assert param.writes == param.display_reads == []


@pytest.mark.parametrize("unicode", [False, True])
@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize("requested", ["-12 dB", "\u221212 dB", "-34 dB"])
def test_finite_db_conversion_handles_infinite_sampled_endpoints(
    bridge_module, unicode, descending, requested
):
    param = gain(unicode=unicode, descending=descending)
    native, warning = bridge_module._parameter_value(param, requested)
    wanted = -34 if "34" in requested else -12
    assert native * 35 * (-1 if descending else 1) == pytest.approx(wanted, abs=0.18)
    assert "approximate" in warning
    assert len(param.display_reads) <= 41
    assert param.writes == []


@pytest.mark.parametrize("request", ["-inf dB", "\u2212\u221e dB", "-INF db"])
@pytest.mark.parametrize("descending", [False, True])
def test_explicit_minus_infinity_selects_only_the_matching_endpoint(
    bridge_module, request, descending
):
    param = gain(unicode=True, descending=descending)
    assert bridge_module._parameter_value(param, request) == (1 if descending else -1, None)
    assert param.writes == []


@pytest.mark.parametrize("descending", [False, True])
def test_plus_infinity_endpoint_also_resolves_for_finite_and_explicit_requests(
    bridge_module, descending
):
    def display(value):
        directed = 1 - value if descending else value
        return "+\u221e dB" if directed == 1 else f"{directed * 24:.2f} dB"

    param = Parameter(0, 1, display)
    assert bridge_module._parameter_value(param, "+inf dB") == (0 if descending else 1, None)
    native, warning = bridge_module._parameter_value(param, "12 dB")
    assert native == pytest.approx(0.5, abs=0.001)
    assert warning


def test_finite_endpoint_request_is_available_without_speculative_writes(bridge_module):
    param = gain()
    assert bridge_module._parameter_value(param, "35 dB")[0] == 1
    assert param.writes == []


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, "1e999", "-1e999"])
def test_nonfinite_native_values_are_rejected_without_writing(bridge_module, value):
    param = gain()
    with pytest.raises(ValueError, match="finite"):
        bridge_module._parameter_value(param, value)
    assert param.writes == []


@pytest.mark.parametrize("request", ["inf dB", "-inf Hz", "NaN dB", "-inf", "-12 Hz"])
def test_invalid_or_unmatched_display_requests_fail_without_writing(bridge_module, request):
    param = gain()
    with pytest.raises(ValueError):
        bridge_module._parameter_value(param, request)
    assert param.writes == []


def test_display_numeric_overflow_cannot_impersonate_an_infinite_endpoint(bridge_module):
    param = gain()
    with pytest.raises(ValueError, match="finite"):
        bridge_module._parameter_value(param, "-" + "9" * 400 + " dB")
    assert param.writes == []


@pytest.mark.parametrize("display", [lambda value: f"{math.sin(value * 6.28)} Hz", lambda _: "1 Hz"])
def test_nonmonotonic_or_constant_displays_still_fail_without_writing(bridge_module, display):
    param = Parameter(0, 1, display)
    with pytest.raises(ValueError, match="not monotonic"):
        bridge_module._parameter_value(param, "0.2 Hz")
    assert param.writes == []


def configure_bridge(bridge, param):
    device = SimpleNamespace(name="Fixture", parameters=[param])
    rack = SimpleNamespace(name="Rack", can_have_chains=True, chains=[SimpleNamespace(devices=[device])])
    bridge._song.tracks = [SimpleNamespace(devices=[device, rack])]
    bridge._song.return_tracks = [SimpleNamespace(devices=[device])]
    bridge._song.master_track = SimpleNamespace(devices=[device])


SETTERS = [
    ("set_device_parameter", {"track_index": 0, "device_index": 0}),
    ("set_return_device_parameter", {"return_index": 0, "device_index": 0}),
    ("set_master_device_parameter", {"device_index": 0}),
    (
        "set_chain_device_parameter",
        {"track_index": 0, "device_index": 1, "chain_index": 0, "chain_device_index": 0},
    ),
]


@pytest.mark.parametrize("command, indices", SETTERS)
@pytest.mark.parametrize("requested", ["8", 8])
def test_all_device_locations_preserve_numeric_enum_semantics_and_requested_value(
    bridge, command, indices, requested
):
    param = enum(["0.5", "1", "2", "3", "4", "5", "6", "8", "12", "16"])
    configure_bridge(bridge, param)
    response = bridge.dispatch(
        {"type": command, "params": dict(indices, parameter="Value", value=requested)}
    )
    assert response["status"] == "success"
    result = response["result"]
    expected = 7 if isinstance(requested, str) else 8
    assert param.writes == [expected]
    assert result["native"] == result["value"] == expected
    assert result["requested"] == requested
    assert result["display"] == ("8" if isinstance(requested, str) else "12")
    assert bool(result["warning"]) is isinstance(requested, str)


@pytest.mark.parametrize("command, indices", SETTERS)
@pytest.mark.parametrize("requested", ["-0.5", "-12 dB", "\u2212\u221e dB"])
def test_all_device_locations_share_gain_conversion(bridge, command, indices, requested):
    param = gain(unicode=True)
    configure_bridge(bridge, param)
    response = bridge.dispatch(
        {"type": command, "params": dict(indices, parameter=0, value=requested)}
    )
    assert response["status"] == "success"
    result = response["result"]
    expected = {"-0.5": -0.5, "-12 dB": -12 / 35, "\u2212\u221e dB": -1}[requested]
    assert len(param.writes) == 1
    assert result["native"] == pytest.approx(expected, abs=0.003)
    assert result["requested"] == requested
    assert "display" in result and "warning" in result


@pytest.mark.parametrize("command, indices", SETTERS)
def test_ambiguous_label_is_rejected_at_every_location_before_any_write(bridge, command, indices):
    param = enum(["8", "8"])
    configure_bridge(bridge, param)
    response = bridge.dispatch(
        {"type": command, "params": dict(indices, parameter=0, value="8")}
    )
    assert response["status"] == "error"
    assert "ambiguous" in response["message"]
    assert param.writes == []
