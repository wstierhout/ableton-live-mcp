"""write_automation: per-point step lengths and linear ramps (no Live needed)."""

import ast
import asyncio
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "ableton_live_mcp/remote_script/__init__.py"
TREE = ast.parse(SCRIPT_PATH.read_text())
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "AbletonMCP")
MODULE_NAMES = {
    "AUTOMATION_RAMP_STEP",
    "MAX_AUTOMATION_STEPS",
    "_finite_number",
    "_automation_steps",
}


def _module_node(node):
    if isinstance(node, ast.FunctionDef):
        return node.name in MODULE_NAMES
    if isinstance(node, ast.Assign):
        return any(getattr(t, "id", None) in MODULE_NAMES for t in node.targets)
    return False


METHOD = next(
    n for n in CLASS.body if isinstance(n, ast.FunctionDef) and n.name == "_write_automation"
)
NAMESPACE = {"math": math}
exec(
    compile(
        ast.Module(body=[n for n in TREE.body if _module_node(n)] + [METHOD], type_ignores=[]),
        str(SCRIPT_PATH),
        "exec",
    ),
    NAMESPACE,
)
steps_for = NAMESPACE["_automation_steps"]
write_automation = NAMESPACE["_write_automation"]
MAX_STEPS = NAMESPACE["MAX_AUTOMATION_STEPS"]


def _sample(steps, t, baseline=None):
    """Read a staircase the way Live 11's value_at_time does: at the exact start of a
    step it returns the value before it, and past a step's end it is back at the
    baseline. Zero-length steps are never visible."""
    for start, length, value in reversed(steps):
        if start < t <= start + length:
            return value
    return baseline


def test_points_mode_defaults_to_zero_length_steps():
    assert steps_for([{"time": 0, "value": 0.5}, {"time": 2, "value": 0.7}]) == [
        (0.0, 0.0, 0.5),
        (2.0, 0.0, 0.7),
    ]


def test_points_mode_uses_each_points_duration():
    steps = steps_for([{"time": 0, "value": 0.5, "duration": 4}, {"time": 4, "value": 0.7}])
    assert steps == [(0.0, 4.0, 0.5), (4.0, 0.0, 0.7)]


def test_ramp_is_contiguous_linear_and_hits_both_ends():
    points = [{"time": 0, "value": 0.2}, {"time": 16, "value": 0.9}]
    steps = steps_for(points, "ramp", 0.0625, hold_until=32.0)
    assert len(steps) == 16 / 0.0625 + 1
    for (t, length, _), (next_t, _, _) in zip(steps, steps[1:]):
        assert t + length == pytest.approx(next_t, abs=1e-9)
    values = [v for _, _, v in steps]
    assert values == sorted(values)
    assert steps[0] == (0.0, 0.0625, 0.2)
    assert steps[-2] == (15.9375, 0.0625, 0.9)  # the ramp itself finishes on 0.9
    assert steps[-1] == (16.0, 16.0, 0.9)  # then holds until the clip end
    for t in (0.001, 3.3, 8.03, 15.99):
        assert _sample(steps, t) == pytest.approx(0.2 + 0.7 * t / 16, abs=0.7 / 255 + 1e-9)


def test_ramp_ending_at_the_clip_end_reaches_its_final_value():
    """Found on Live 11.3.43: the 0-length hold at the clip end is invisible, so the
    ramp topped out one step short (119.6875 instead of 120 at 1/16 beat, and a full
    step short at coarse resolutions)."""
    for step in (0.0625, 4.0):
        points = [{"time": 0, "value": 40}, {"time": 16, "value": 120}]
        steps = steps_for(points, "ramp", step, hold_until=16.0)
        assert steps[-1] == (16.0, 0.0, 120.0)
        assert _sample(steps, 15.999) == 120.0
        assert _sample(steps, 0.001) == 40.0


def test_ramp_steps_fit_spans_that_do_not_divide_evenly():
    steps = steps_for([{"time": 0, "value": 0}, {"time": 1, "value": 1}], "ramp", 0.3)
    assert [round(s[1], 9) for s in steps[:-1]] == [0.25] * 4
    assert [round(s[2], 9) for s in steps[:-1]] == [0, 0.333333333, 0.666666667, 1]
    assert steps[-2][0] + steps[-2][1] == 1.0
    assert steps[-1] == (1.0, 0.0, 1.0)  # no clip end known: last point is momentary


def test_ramp_last_point_duration_and_instant_jumps():
    saw = [
        {"time": 0, "value": 0},
        {"time": 4, "value": 1},
        {"time": 4, "value": 0},
        {"time": 8, "value": 1, "duration": 2},
    ]
    steps = steps_for(saw, "ramp", 1.0, hold_until=100.0)
    assert _sample(steps, 3.5) == 0.75  # earlier segments: value at each step's start
    assert _sample(steps, 4.0) == 0.75  # Live reads the value before the jump...
    assert _sample(steps, 4.001) == 0.0  # ...and the next segment starts on its value
    assert _sample(steps, 7.5) == 1.0  # the last segment finishes on the final value
    assert steps[-1] == (8.0, 2.0, 1.0)


@pytest.mark.parametrize(
    "points, mode, step, message",
    [
        ([], "points", 0.0625, "non-empty"),
        ([{"time": 0}], "points", 0.0625, "needs 'time' and 'value'"),
        ([{"time": -1, "value": 0}], "points", 0.0625, ">= 0"),
        ([{"time": 0, "value": float("nan")}], "points", 0.0625, "finite"),
        ([{"time": 0, "value": 2}], "points", 0.0625, "outside the parameter range"),
        ([{"time": 0, "value": 0, "duration": -1}], "points", 0.0625, ">= 0"),
        ([{"time": 0, "value": 0}], "ramp", 0.0625, "at least two"),
        ([{"time": 4, "value": 0}, {"time": 0, "value": 1}], "ramp", 0.0625, "time order"),
        (
            [{"time": 0, "value": 0, "duration": 1}, {"time": 4, "value": 1}],
            "ramp",
            0.0625,
            "only the last point",
        ),
        ([{"time": 0, "value": 0}, {"time": 4, "value": 1}], "ramp", 0, "greater than 0"),
        ([{"time": 0, "value": 0}, {"time": 4, "value": 1}], "curve", 0.0625, "mode must be"),
        (
            [{"time": 0, "value": 0}, {"time": MAX_STEPS, "value": 1}],
            "ramp",
            1.0,
            "larger step_length",
        ),
    ],
)
def test_bad_requests_are_rejected(points, mode, step, message):
    with pytest.raises(ValueError, match=message):
        steps_for(points, mode, step, value_range=(0.0, 1.0))


class FakeEnvelope:
    def __init__(self):
        self.steps = []
        self.initial = None

    def insert_step(self, time, length, value):
        self.steps.append((time, length, value))

    def value_at_time(self, t):
        return _sample(self.steps, t, baseline=self.initial)


class FakeClip:
    end_marker = 8.0
    loop_end = 16.0

    def __init__(self):
        self.envelope = FakeEnvelope()
        self.cleared = []

    def clear_envelope(self, param):
        self.cleared.append(param)

    def automation_envelope(self, param):
        return None  # no envelope yet: forces create_automation_envelope

    def create_automation_envelope(self, param):
        # Like Live 11: the new envelope starts from the value the parameter had at
        # Live's last update, which can lag a value set in the same update.
        self.created_from = getattr(param, "applied", param.value)
        self.envelope = FakeEnvelope()
        self.envelope.initial = self.created_from
        return self.envelope


def _bridge(clip, param=None):
    param = param or SimpleNamespace(name="Filter Freq", min=0.0, max=1.0, value=0.7)
    device = SimpleNamespace(name="Auto Filter")
    return SimpleNamespace(
        _get_clip=lambda *_: clip,
        _get_device=lambda *_: device,
        _resolve_parameter=lambda *_: param,
        _song=SimpleNamespace(view=SimpleNamespace(detail_clip=None)),
    )


def test_write_automation_ramps_and_holds_to_the_clip_end():
    clip = FakeClip()
    points = [{"time": 0, "value": 0.2}, {"time": 4, "value": 0.9}]
    result = write_automation(_bridge(clip), 0, 0, 0, "Filter Freq", points, "ramp", 0.25)
    assert clip.cleared and clip.envelope.steps[0] == (0.0, 0.25, 0.2)
    assert clip.envelope.steps[-1] == (4.0, 12.0, 0.9)  # holds to max(end_marker, loop_end)
    assert result["step_count"] == len(clip.envelope.steps) == 17
    assert (result["start"], result["end"], result["mode"]) == (0.0, 16.0, "ramp")


def test_ramp_from_beat_zero_starts_the_envelope_on_its_first_value():
    """Found on Live 11.3.43: a new envelope starts from the parameter's current
    value and Live keeps it at the exact clip start, which clicked on every loop."""
    clip = FakeClip()
    points = [{"time": 0, "value": 0.2}, {"time": 4, "value": 0.9}]
    result = write_automation(_bridge(clip), 0, 0, 0, "Filter Freq", points, "ramp", 0.25)
    assert clip.created_from == 0.2 and result["start_value_set"] == 0.2


@pytest.mark.parametrize(
    "points, mode",
    [
        ([{"time": 0, "value": 0.2, "duration": 4}], "points"),
        ([{"time": 2, "value": 0.2}, {"time": 4, "value": 0.9}], "ramp"),
    ],
)
def test_parameter_is_left_alone_unless_a_ramp_starts_at_beat_zero(points, mode):
    clip = FakeClip()
    result = write_automation(_bridge(clip), 0, 0, 0, "Filter Freq", points, mode)
    assert clip.created_from == 0.7 and result["start_value_set"] is None


def test_start_settled_reports_whether_beat_zero_reads_the_first_value():
    """Found on Live 11.3.43: setting the parameter and creating the envelope in one
    update left beat 0 at the old value; a second write after an update was clean."""
    param = SimpleNamespace(name="Frequency", min=20.0, max=135.0, value=127.0, applied=127.0)
    clip = FakeClip()
    points = [{"time": 0, "value": 40}, {"time": 16, "value": 120}]
    first = write_automation(_bridge(clip, param), 0, 0, 0, "Frequency", points, "ramp")
    assert first["start_value_set"] == 40.0 and first["start_settled"] is False
    assert clip.envelope.value_at_time(0.0) == 127.0
    param.applied = param.value  # Live's next update
    second = write_automation(_bridge(clip, param), 0, 0, 0, "Frequency", points, "ramp")
    assert second["start_settled"] is True and clip.envelope.value_at_time(0.0) == 40.0
    assert clip.envelope.value_at_time(15.999) == 120.0


def test_start_settled_is_only_reported_for_ramps_from_beat_zero():
    clip = FakeClip()
    points = [{"time": 0, "value": 0.2, "duration": 4}]
    assert write_automation(_bridge(clip), 0, 0, 0, "F", points)["start_settled"] is None


def test_a_parameter_that_refuses_the_start_value_still_gets_its_ramp():
    class Stubborn:
        name, min, max = "Locked", 0.0, 1.0

        @property
        def value(self):
            return 0.7

        @value.setter
        def value(self, _):
            raise RuntimeError("parameter is disabled")

    clip = FakeClip()
    points = [{"time": 0, "value": 0.2}, {"time": 4, "value": 0.9}]
    result = write_automation(_bridge(clip, Stubborn()), 0, 0, 0, "Locked", points, "ramp")
    assert result["start_value_set"] is None and result["start_settled"] is None
    assert len(clip.envelope.steps) == 65


def test_write_automation_default_call_is_unchanged():
    clip = FakeClip()
    write_automation(_bridge(clip), 0, 0, 0, "Filter Freq", [{"time": 1, "value": 0.5}])
    assert clip.envelope.steps == [(1.0, 0.0, 0.5)]


def test_invalid_write_leaves_the_existing_envelope_alone():
    clip = FakeClip()
    with pytest.raises(ValueError):
        write_automation(_bridge(clip), 0, 0, 0, "Filter Freq", [{"time": 0, "value": 5}])
    assert clip.cleared == [] and clip.envelope.steps == []


def test_dispatch_passes_mode_and_step_length():
    """The same row serves direct calls and batch_commands."""
    table = next(
        n
        for n in CLASS.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "_MUTATING_COMMANDS"
    )
    row = next(
        v for k, v in zip(table.value.keys, table.value.values) if k.value == "write_automation"
    )
    handler = eval(compile(ast.Expression(row), "row", "eval"), NAMESPACE)
    seen = []
    bridge = SimpleNamespace(_req=lambda p, k: p[k], _write_automation=lambda *a: seen.append(a))
    base = {"track_index": 0, "clip_index": 0, "device_index": 0, "parameter": 1, "points": []}
    handler(bridge, base)
    handler(bridge, {**base, "mode": "ramp", "step_length": 0.25})
    assert seen[0][5:] == ("points", NAMESPACE["AUTOMATION_RAMP_STEP"])
    assert seen[1][5:] == ("ramp", 0.25)


def test_tool_sends_mode_and_flags_an_outdated_remote_script(monkeypatch):
    from ableton_live_mcp import tools as _tools  # noqa: F401
    from ableton_live_mcp.app import mcp
    from ableton_live_mcp.tools import arrangement

    sent = []
    replies = [
        {
            "parameter": "Freq",
            "device": "Filter",
            "mode": "ramp",
            "point_count": 2,
            "step_count": 17,
            "start": 0.0,
            "end": 16.0,
            "start_value_set": 40.0,
        },
        {"parameter": "Freq", "point_count": 2},  # what a 1.8.1 Remote Script returns
    ]
    connection = SimpleNamespace(send_command=lambda c, p: sent.append((c, p)) or replies.pop(0))
    monkeypatch.setattr(arrangement, "get_ableton_connection", lambda: connection)
    points = [{"time": 0, "value": 0.2}, {"time": 4, "value": 0.9}]
    message = arrangement.write_automation(None, 0, 0, 0, "Freq", points, "ramp")
    assert "17 steps" in message and "set Freq to 40.0" in message
    assert sent[0][1]["mode"] == "ramp" and sent[0][1]["step_length"] == 0.0625
    assert "older than this server" in arrangement.write_automation(None, 0, 0, 0, "Freq", points)

    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "write_automation")
    props = tool.inputSchema["properties"]
    assert all(props[k].get("description") for k in props)
    assert set(props["mode"]["enum"]) == {"points", "ramp"}


def _tool(monkeypatch, replies):
    from ableton_live_mcp.tools import arrangement

    sent, slept = [], []
    connection = SimpleNamespace(send_command=lambda c, p: sent.append((c, p)) or replies.pop(0))
    monkeypatch.setattr(arrangement, "get_ableton_connection", lambda: connection)
    monkeypatch.setattr(arrangement.time, "sleep", slept.append)
    return arrangement.write_automation, sent, slept


RAMP_REPLY = {
    "parameter": "Frequency",
    "device": "Auto Filter",
    "mode": "ramp",
    "point_count": 2,
    "step_count": 257,
    "start": 0.0,
    "end": 16.0,
    "start_value_set": 40.0,
}


def test_tool_rewrites_until_beat_zero_settles(monkeypatch):
    replies = [{**RAMP_REPLY, "start_settled": False}, {**RAMP_REPLY, "start_settled": True}]
    write, sent, slept = _tool(monkeypatch, replies)
    points = [{"time": 0, "value": 40}, {"time": 16, "value": 120}]
    message = write(None, 0, 1, 1, "Frequency", points, "ramp")
    assert len(sent) == 2 and sent[0] == sent[1] and slept == [0.05]
    assert "rewrote the ramp 1x" in message and "Warning" not in message


def test_tool_warns_when_beat_zero_never_settles(monkeypatch):
    write, sent, slept = _tool(monkeypatch, [{**RAMP_REPLY, "start_settled": False}] * 4)
    message = write(None, 0, 1, 1, "Frequency", [{"time": 0, "value": 40}], "ramp")
    assert len(sent) == 4 and slept == [0.05, 0.2, 0.5]
    assert "Warning: after 3 rewrites beat 0 still starts from the old value" in message


def test_tool_does_not_rewrite_settled_or_point_writes(monkeypatch):
    replies = [{**RAMP_REPLY, "start_settled": True}, {**RAMP_REPLY, "start_settled": None}]
    write, sent, slept = _tool(monkeypatch, replies)
    write(None, 0, 1, 1, "Frequency", [{"time": 0, "value": 40}], "ramp")
    write(None, 0, 1, 1, "Frequency", [{"time": 0, "value": 40}])
    assert len(sent) == 2 and slept == []
