"""Regression coverage for PRs 4/5 through the real Live bridge dispatcher.

The envelope double models only the public insert/read API. These tests prove
validation, scheduling, and command contracts; actual Live acceptance remains a
separate Windows/macOS check.
"""

import asyncio
from types import SimpleNamespace

import pytest

from ableton_live_mcp.tools import arrangement, session


TARGET = {"track_index": 0, "clip_index": 0, "device_index": 0, "parameter": "Gain"}


def dispatch(bridge, command, **params):
    return bridge.dispatch({"type": command, "params": {**TARGET, **params}})


def test_points_write_real_durations_through_the_clip_end(bridge, automation):
    reply = dispatch(
        bridge,
        "write_automation",
        points=[{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
    )
    assert reply["status"] == "success", reply
    assert automation.envelope.steps == [(0.0, 1.0, 0.2), (1.0, 3.0, 0.8)]
    assert automation.events.count(("clear",)) == 1


def test_explicit_durations_preserve_intentional_gaps(bridge, automation):
    reply = dispatch(
        bridge,
        "write_automation",
        points=[
            {"time": 0, "duration": 0.5, "value": 0.2},
            {"time": 2, "duration": 1, "value": 0.8},
        ],
    )
    assert reply["status"] == "success", reply
    assert automation.envelope.steps == [(0.0, 0.5, 0.2), (2.0, 1.0, 0.8)]


@pytest.mark.parametrize(
    "points, options",
    [
        ([], {}),
        ([{"time": True, "value": 0.2}], {}),
        ([{"time": "0", "value": 0.2}], {}),
        ([{"time": float("nan"), "value": 0.2}], {}),
        ([{"time": -0.1, "value": 0.2}], {}),
        ([{"time": 4.1, "value": 0.2}], {}),
        ([{"time": 0, "value": True}], {}),
        ([{"time": 0, "value": "0.2"}], {}),
        ([{"time": 0, "value": float("inf")}], {}),
        ([{"time": 0, "value": 1.1}], {}),
        ([{"time": 0, "value": 0.2, "duration": 0}], {}),
        ([{"time": 0, "value": 0.2, "duration": -1}], {}),
        ([{"time": 0, "value": 0.2, "duration": True}], {}),
        ([{"time": 3, "value": 0.2, "duration": 2}], {}),
        ([{"time": 2, "value": 0.2}, {"time": 1, "value": 0.8}], {}),
        ([{"time": 0, "value": 0.2}, {"time": 0, "value": 0.8}], {}),
        (
            [
                {"time": 0, "value": 0.2, "duration": 2},
                {"time": 1, "value": 0.8},
            ],
            {},
        ),
        ([{"time": 0, "value": 0.2}], {"mode": "unknown"}),
        ([{"time": 0, "value": 0.2}], {"step_length": 0}),
        (
            [{"time": 0, "value": 0.2}, {"time": 4, "value": 0.8}],
            {"mode": "ramp", "step_length": 0.0001},
        ),
        (
            [
                {"time": 0, "value": 0.2, "duration": 0.5},
                {"time": 1, "value": 0.8},
            ],
            {"mode": "ramp"},
        ),
    ],
)
def test_invalid_writes_do_not_clear_seed_or_replace_existing_automation(
    bridge, automation, points, options
):
    before = list(automation.envelope.steps)
    reply = dispatch(bridge, "write_automation", points=points, **options)
    assert reply["status"] == "error", reply
    assert automation.envelope.steps == before
    assert automation.parameter.value == 0.25
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)


def test_final_point_at_clip_end_does_not_insert_a_zero_length_step(bridge, automation):
    reply = dispatch(
        bridge,
        "write_automation",
        points=[{"time": 0, "value": 0.2}, {"time": 4, "value": 0.8}],
    )
    assert reply["status"] == "success", reply
    assert automation.envelope.steps == [(0.0, 4.0, 0.2)]


def test_ramp_finishes_its_final_value_before_the_end_and_inserts_only_once(bridge, automation):
    reply = dispatch(
        bridge,
        "write_automation",
        mode="ramp",
        step_length=0.25,
        points=[{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
    )
    assert reply["status"] == "success", reply
    steps = automation.envelope.steps
    assert steps[0][0] == 0 and steps[0][2] == 0.2
    assert any(time < 1 and value == 0.8 for time, duration, value in steps)
    assert all(duration > 0 for time, duration, value in steps)
    assert all(0 <= time < time + duration <= 4 for time, duration, value in steps)
    assert automation.events.count(("clear",)) == 1
    assert len([event for event in automation.events if event[0] == "insert"]) == len(steps)


def test_ramps_allow_an_instantaneous_equal_time_jump(bridge, automation):
    reply = dispatch(
        bridge,
        "write_automation",
        mode="ramp",
        step_length=0.25,
        points=[
            {"time": 0, "value": 0.0},
            {"time": 1, "value": 0.4},
            {"time": 1, "value": 0.8},
            {"time": 2, "value": 1.0},
        ],
    )
    assert reply["status"] == "success", reply
    assert automation.envelope.value_at_time(1) == pytest.approx(0.8)
    assert all(duration > 0 for _, duration, _ in automation.envelope.steps)


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_beat_zero_write_waits_a_live_tick_before_replacing_the_envelope(
    bridge, bridge_module, automation, mode
):
    deferred = bridge._write_automation(
        0,
        0,
        0,
        "Gain",
        [{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
        mode=mode,
        step_length=0.25,
    )
    assert isinstance(deferred, bridge_module._Deferred)
    finished = []
    deferred.start(lambda result=None, error=None: finished.append((result, error)))
    assert automation.parameter.value == 0.2
    assert ("seed", 0.2) in automation.events
    assert not any(event[0] in {"clear", "create", "insert"} for event in automation.events)
    assert finished == [] and bridge.pending
    while bridge.pending:
        bridge.pending.pop(0)()
    assert len(finished) == 1 and finished[0][1] is None
    assert automation.events.count(("clear",)) == 1
    assert automation.envelope.steps


def test_oversized_points_request_preserves_the_envelope(bridge, automation):
    points = [{"time": index / 2048, "value": 0.5} for index in range(4097)]
    reply = dispatch(bridge, "write_automation", points=points)
    assert reply["status"] == "error", reply
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)


def test_batch_writes_a_nonzero_start_ramp_and_reads_it_in_one_undo_step(bridge, automation):
    reply = bridge.dispatch(
        {
            "type": "batch",
            "params": {
                "commands": [
                    {
                        "type": "write_automation",
                        "params": {
                            **TARGET,
                            "mode": "ramp",
                            "step_length": 0.25,
                            "points": [{"time": 0.25, "value": 0.2}, {"time": 1.25, "value": 0.8}],
                        },
                    },
                    {"type": "read_automation", "params": {**TARGET, "times": [0.25, 1.0]}},
                ]
            },
        }
    )
    assert reply["status"] == "success", reply
    assert reply["result"]["executed"] == 2
    sampled = reply["result"]["results"][1]["result"]["samples"]
    assert [(item["time"], item["value"]) for item in sampled] == [(0.25, 0.2), (1.0, 0.8)]
    assert automation.envelope.value_at_time(1.0) == pytest.approx(0.8)
    assert bridge.edits == ["begin", "end"]


def test_read_explicit_times_samples_existing_envelope_without_edits(bridge, automation):
    reply = dispatch(bridge, "read_automation", times=[0, 1, 4])
    assert reply["status"] == "success", reply
    assert automation.envelope.reads == [0.0, 1.0, 4.0]
    result = reply["result"]
    assert result["has_envelope"] is True
    assert result["parameter"] == "Gain"
    assert result["device"] == "Utility"
    assert result["min"] == 0 and result["max"] == 1
    assert result["current_value"] == 0.25
    assert [(sample["time"], sample["value"]) for sample in result["samples"]] == [
        (0.0, 0.5),
        (1.0, 0.5),
        (4.0, 0.5),
    ]
    assert automation.events == [("lookup",)]
    assert bridge.edits == []


def test_read_grid_includes_end_without_duplicate_samples(bridge, automation):
    reply = dispatch(bridge, "read_automation", start=0, end=1, step=0.25)
    assert reply["status"] == "success", reply
    assert automation.envelope.reads == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert automation.events == [("lookup",)]


@pytest.mark.parametrize(
    "options",
    [
        {"times": [True]},
        {"times": ["0"]},
        {"times": [float("inf")]},
        {"times": [-0.1]},
        {"times": [4.1]},
        {"times": [0] * 1025},
        {"times": [0], "start": 0, "end": 1},
        {"start": 0, "end": 1, "step": 0},
        {"start": 2, "end": 1, "step": 0.25},
        {"start": 0, "end": 5, "step": 0.25},
        {"start": 0, "end": 4, "step": 0.001},
        {"start": float("nan"), "end": 1, "step": 0.25},
    ],
)
def test_invalid_read_requests_never_sample_or_create_an_envelope(bridge, automation, options):
    reply = dispatch(bridge, "read_automation", **options)
    assert reply["status"] == "error", reply
    assert automation.envelope.reads == []
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)
    assert bridge.edits == []


def test_read_absent_envelope_never_creates_one(bridge, automation):
    automation.clip.envelope = None
    reply = dispatch(bridge, "read_automation", times=[0, 1])
    assert reply["status"] == "success", reply
    assert automation.clip.envelope is None
    assert automation.events == [("lookup",)]
    assert automation.parameter.value == 0.25
    assert reply["result"]["has_envelope"] is False
    assert reply["result"]["samples"] == []


def test_read_automation_is_classified_as_read_only_in_both_layers(bridge):
    from ableton_live_mcp.app import mcp

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert tools["read_automation"].annotations.readOnlyHint is True
    assert tools["write_automation"].annotations.readOnlyHint is not True
    assert "read_automation" in bridge._READONLY_COMMANDS
    assert "read_automation" not in bridge._MUTATING_COMMANDS


@pytest.mark.parametrize("tool_name", ["write_automation", "read_automation"])
def test_new_automation_contracts_fail_before_command_on_stale_bridge(monkeypatch, tool_name):
    requested = []

    def unsupported(capability):
        requested.append(capability)
        raise RuntimeError("Install the matching Remote Script")

    connection = SimpleNamespace(
        require_capability=unsupported,
        send_command=lambda *args: pytest.fail("An old bridge must not receive the new command"),
    )
    monkeypatch.setattr(arrangement, "get_ableton_connection", lambda: connection)
    kwargs = dict(TARGET)
    if tool_name == "write_automation":
        kwargs["points"] = [{"time": 0, "value": 0.5}]
    else:
        kwargs["times"] = [0]
    with pytest.raises(RuntimeError, match="matching Remote Script"):
        getattr(arrangement, tool_name)(**kwargs)
    assert requested == [
        "automation_v2" if tool_name == "write_automation" else "automation_readback"
    ]


@pytest.mark.parametrize(
    "command, capability, payload",
    [
        ("write_automation", "automation_v2", {"points": [{"time": 0, "value": 0.5}]}),
        ("read_automation", "automation_readback", {"times": [0]}),
    ],
)
def test_batch_automation_capabilities_are_checked_before_any_command(
    monkeypatch, command, capability, payload
):
    requested = []

    def require_capability(name):
        requested.append(name)
        if name != "batch_preflight":
            raise RuntimeError("Install the matching Remote Script")

    connection = SimpleNamespace(
        require_capability=require_capability,
        send_command=lambda *args, **kwargs: pytest.fail("A stale batch must not be submitted"),
    )
    monkeypatch.setattr(session, "get_ableton_connection", lambda: connection)
    commands = [
        {"type": "create_midi_track"},
        {"type": command, "params": {**TARGET, **payload}},
    ]
    with pytest.raises(RuntimeError, match="matching Remote Script"):
        session.batch_commands(commands)
    assert requested == ["batch_preflight", capability]
