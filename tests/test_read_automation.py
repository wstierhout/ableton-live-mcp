"""read_automation samples a clip envelope with value_at_time (no Live needed)."""

import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "ableton_live_mcp/remote_script/__init__.py"
CLASS = next(
    n
    for n in ast.parse(SCRIPT_PATH.read_text()).body
    if isinstance(n, ast.ClassDef) and n.name == "AbletonMCP"
)
METHOD = next(
    n for n in CLASS.body if isinstance(n, ast.FunctionDef) and n.name == "_read_automation"
)
NAMESPACE = {"math": math}
exec(compile(ast.Module(body=[METHOD], type_ignores=[]), str(SCRIPT_PATH), "exec"), NAMESPACE)
read_automation = NAMESPACE["_read_automation"]


class RampEnvelope:
    """A 0.2 -> 0.9 ramp over beats 0-16, then held."""

    def value_at_time(self, t):
        return 0.2 + 0.7 * min(t, 16.0) / 16.0


def _bridge(envelope, end_marker=16.0, loop_end=8.0):
    param = SimpleNamespace(
        name="Filter Freq", min=0.0, max=1.0, value=0.5, str_for_value=lambda v: f"{v:.2f}"
    )
    clip = SimpleNamespace(
        end_marker=end_marker, loop_end=loop_end, automation_envelope=lambda p: envelope
    )
    return SimpleNamespace(
        MAX_AUTOMATION_SAMPLES=1024,
        _get_clip=lambda *_: clip,
        _get_device=lambda *_: SimpleNamespace(name="Auto Filter"),
        _resolve_parameter=lambda *_: param,
    )


def test_default_grid_samples_every_beat_to_the_clip_end():
    r = read_automation(_bridge(RampEnvelope()), 0, 0, 0, "Filter Freq")
    assert r["has_envelope"] and [s["time"] for s in r["samples"]] == [float(b) for b in range(17)]
    assert r["samples"][0]["value"] == 0.2
    assert r["samples"][-1]["value"] == pytest.approx(0.9)
    assert r["samples"][8]["display"] == "0.55"


def test_explicit_times_and_custom_grid():
    bridge = _bridge(RampEnvelope())
    r = read_automation(bridge, 0, 0, 0, 1, times=[0, 4.5, 32])
    assert [s["time"] for s in r["samples"]] == [0.0, 4.5, 32.0]
    r = read_automation(bridge, 0, 0, 0, 1, start=2, end=3, step=0.25)
    assert [s["time"] for s in r["samples"]] == [2.0, 2.25, 2.5, 2.75, 3.0]


def test_missing_envelope_and_bad_ranges():
    r = read_automation(_bridge(None), 0, 0, 0, 1)
    assert r == {
        "parameter": "Filter Freq",
        "device": "Auto Filter",
        "min": 0.0,
        "max": 1.0,
        "current_value": 0.5,
        "has_envelope": False,
        "samples": [],
    }
    with pytest.raises(ValueError, match="step > 0"):
        read_automation(_bridge(RampEnvelope()), 0, 0, 0, 1, start=4, end=2)
    with pytest.raises(ValueError, match="limit 1024"):
        read_automation(_bridge(RampEnvelope()), 0, 0, 0, 1, end=2000, step=1)
