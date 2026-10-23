import ast
import math
import os
import re
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from ableton_live_mcp.tools.audio_analysis import measure_wav

SOURCE = (Path(__file__).parents[1] / "ableton_live_mcp/remote_script/__init__.py").read_text()
TREE = ast.parse(SOURCE)
HELPERS = ast.Module(
    body=[
        n
        for n in TREE.body
        if isinstance(n, ast.FunctionDef)
        and n.name in {"_display_number", "_sampled_display", "_parameter_value"}
    ],
    type_ignores=[],
)
NAMESPACE = {"math": math, "re": re}
exec(compile(HELPERS, "parameter_helpers", "exec"), NAMESPACE)
resolve = NAMESPACE["_parameter_value"]


def test_display_mapping_is_read_only_and_handles_logarithmic_hz():
    param = SimpleNamespace(
        min=0, max=1, is_quantized=False, str_for_value=lambda x: f"{20 * 1000**x} Hz"
    )
    value, warning = resolve(param, "1 kHz")
    assert 20 * 1000**value == pytest.approx(1000, rel=0.005)
    assert warning
    for bad in ("-1 Hz", "250 ms", "Auto", float("nan"), float("inf")):
        with pytest.raises(ValueError):
            resolve(param, bad)


def test_native_clamping_and_enum_labels():
    p = SimpleNamespace(
        min=0, max=2, is_quantized=True, str_for_value=lambda x: ["Off", "Sync", "Auto"][int(x)]
    )
    assert resolve(p, "Auto")[0] == 2
    assert resolve(p, 99) == (2.0, "clamped or quantized to native range")
    assert resolve(p, 0.7)[0] == 1
    with pytest.raises(ValueError):
        resolve(p, "missing")


def test_ambiguous_mapping_rejected():
    p = SimpleNamespace(
        min=0, max=1, is_quantized=False, str_for_value=lambda x: f"{math.sin(x * 6.28)} Hz"
    )
    with pytest.raises(ValueError):
        resolve(p, "0.2 Hz")


def test_db_display_resolves_when_the_range_starts_at_minus_inf():
    # Utility Gain/Output and similar parameters read "-inf dB" at the bottom of the range.
    p = SimpleNamespace(
        min=-1,
        max=1,
        is_quantized=False,
        str_for_value=lambda x: "-inf dB" if x <= -1 else f"{x * 35:.2f} dB",
    )
    value, warning = resolve(p, "-12 dB")
    assert value == pytest.approx(-12 / 35, abs=1e-3)
    assert warning


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_wav_measurements_and_partial_window(tmp_path, width):
    path = tmp_path / "measured.wav"
    scale = 2 ** (width * 8 - 1)
    values = [0, scale // 2, -scale // 2, -scale] * 100
    data = b"".join(
        bytes([v + 128]) if width == 1 else v.to_bytes(width, "little", signed=True) for v in values
    )
    with wave.open(str(path), "wb") as out:
        out.setparams((1, width, 100, 0, "NONE", "not compressed"))
        out.writeframes(data)
    result = measure_wav(path, 2)
    assert result["partial"] and result["analyzed_seconds"] == 2
    assert result["evidence"] == "measured_pcm"
    assert result["channels"][0]["sample_peak_dbfs"] == 0
    assert result["channels"][0]["full_scale_samples"] == 50
    assert result["channels"][0]["rms_dbfs"] == pytest.approx(10 * math.log10(0.375), abs=0.0001)


def test_wav_silence_and_limits(tmp_path):
    path = tmp_path / "silent.wav"
    with wave.open(str(path), "wb") as out:
        out.setparams((2, 2, 100, 0, "NONE", "not compressed"))
        out.writeframes(b"\0" * 400)
    result = measure_wav(path)
    assert result["channels"][0]["rms_dbfs"] is None
    assert result["channels"][1]["sample_peak_dbfs"] is None
    assert not result["partial"]
    for duration in (0, 121, float("nan")):
        with pytest.raises(ValueError):
            measure_wav(path, duration)


def test_simpler_gate_and_requested_file_are_checked_before_writing(tmp_path):
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "AbletonMCP")
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "_replace_simpler_sample"
    )
    namespace = {"os": os}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "simpler", "exec"), namespace)
    replace = namespace[method.name]
    calls = []
    device = SimpleNamespace(
        name="Simpler",
        replace_sample=calls.append,
        sample=SimpleNamespace(file_path="readback.wav"),
    )
    bridge = SimpleNamespace(_get_device=lambda *a: device, _live_version=lambda: "12.3.0")
    path = tmp_path / "sample.wav"
    path.write_bytes(b"test fixture; validation of audio format belongs to Live")
    with pytest.raises(ValueError, match="12.4"):
        replace(bridge, 0, 0, str(path))
    bridge._live_version = lambda: "12.4.5"
    with pytest.raises(ValueError, match="absolute"):
        replace(bridge, 0, 0, "relative.wav")
    assert not calls
    result = replace(bridge, 0, 0, str(path))
    assert calls == [str(path)] and result["loaded_path"] == "readback.wav"
