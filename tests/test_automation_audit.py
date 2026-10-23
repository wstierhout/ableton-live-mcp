"""Regression cases identified during the pre-Windows candidate audit."""

import asyncio
import math
from types import SimpleNamespace

import pytest

from ableton_live_mcp.app import mcp
from ableton_live_mcp.tools import arrangement

TARGET = {"track_index": 0, "clip_index": 0, "device_index": 0, "parameter": "Gain"}


def dispatch(bridge, command, **params):
    return bridge.dispatch({"type": command, "params": {**TARGET, **params}})


def test_large_grid_does_not_snap_a_non_endpoint_sample_to_its_end(bridge_module):
    end = 1e12
    start = end - 1
    samples = bridge_module._automation_sample_times(start=start, end=end, step=10, clip_end=end)
    assert samples == [start]


def test_grid_end_outside_clip_is_invalid_even_when_no_sample_reaches_it(bridge_module):
    with pytest.raises(ValueError):
        bridge_module._automation_sample_times(start=0, end=5, step=10, clip_end=4)


def test_a_positive_duration_must_advance_its_start_at_float_precision(bridge_module):
    with pytest.raises(ValueError):
        bridge_module._automation_steps(
            [{"time": 1e15, "value": 0.5, "duration": 1e-10}],
            hold_until=1e15 + 1,
        )


def test_a_ramp_that_cannot_represent_its_required_steps_is_rejected(bridge_module):
    start = 1e15
    end = math.nextafter(start, math.inf)
    with pytest.raises(ValueError):
        bridge_module._automation_steps(
            [{"time": start, "value": 0.0}, {"time": end, "value": 1.0}],
            mode="ramp",
            step_length=4,
            hold_until=end,
        )


def test_large_finite_ramp_values_do_not_overflow_during_interpolation(bridge_module):
    steps = bridge_module._automation_steps(
        [{"time": 0, "value": -1e308}, {"time": 1, "value": 1e308}],
        mode="ramp",
        step_length=0.25,
        hold_until=1,
        value_range=(-1e308, 1e308),
    )
    values = [value for _, _, value in steps]
    assert all(math.isfinite(value) for value in values)
    assert values[0] == -1e308 and values[-1] == 1e308
    assert values == sorted(values)
    assert all(math.isfinite(time) and duration > 0 for time, duration, _ in steps)


@pytest.fixture
def live11_automation(bridge, automation):
    """Model Live 11's previous-update baseline and left-hand step boundaries.

    Envelope creation captures the parameter's last completed Live update.
    Inserting a step at zero does not change the value returned exactly at zero.
    A seed assignment only reaches that cache when a later Live tick runs.
    """
    parameter = automation.parameter
    parameter.cached_value = parameter.value
    events = automation.events
    created = []

    class Live11Envelope:
        def __init__(self, baseline):
            self.baseline = baseline
            self.steps = []

        def insert_step(self, time, duration, value):
            events.append(("insert", time, duration, value))
            self.steps.append((time, duration, value))
            parameter.automation_state = 1

        def value_at_time(self, time):
            if time == 0:
                return self.baseline
            for start, duration, value in reversed(self.steps):
                if start < time <= start + duration:
                    return value
            return self.baseline

    automation.clip.envelope = Live11Envelope(parameter.cached_value)

    def clear(param):
        assert param is parameter
        events.append(("clear",))
        automation.clip.envelope = None
        parameter.automation_state = 0

    def create(param):
        assert param is parameter
        envelope = Live11Envelope(parameter.cached_value)
        events.append(("create", envelope.baseline))
        created.append(envelope)
        automation.clip.envelope = envelope
        return envelope

    schedule = bridge.schedule_message

    def schedule_message(delay, callback):
        def after_live_update():
            parameter.cached_value = parameter.value
            events.append(("tick", delay, parameter.cached_value))
            callback()

        schedule(delay, after_live_update)

    automation.clip.clear_envelope = clear
    automation.clip.create_automation_envelope = create
    bridge.schedule_message = schedule_message
    return SimpleNamespace(parameter=parameter, created=created, events=events)


def assert_seed_settled_before_creation(model):
    assert len(model.created) == 1
    assert model.created[0].baseline == 0.2
    seed_index = next(i for i, event in enumerate(model.events) if event[0] == "seed")
    create_index = next(i for i, event in enumerate(model.events) if event[0] == "create")
    assert seed_index < create_index
    assert any(
        event[0] == "tick" and event[1] > 0 for event in model.events[seed_index + 1 : create_index]
    )


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_live11_beat_zero_write_seeds_a_live_update_before_creating_its_envelope(
    bridge, live11_automation, mode
):
    reply = dispatch(
        bridge,
        "write_automation",
        mode=mode,
        step_length=0.25,
        points=[{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
    )
    assert reply["status"] == "success", reply
    assert reply["result"]["start_settled"] is True
    assert reply["result"]["prior_value"] == 0.25
    assert reply["result"]["prior_automation_state"] == 0
    assert reply["result"]["automation_state"] == 1
    assert_seed_settled_before_creation(live11_automation)
    read = dispatch(bridge, "read_automation", times=[0, 0.0001, 0.75, 0.7501, 1.0001])
    assert read["status"] == "success", read
    expected = [0.2, 0.2, 0.6, 0.8, 0.8] if mode == "ramp" else [0.2, 0.2, 0.2, 0.2, 0.8]
    assert [sample["value"] for sample in read["result"]["samples"]] == pytest.approx(expected)


def test_live11_nonzero_start_batch_reads_actual_boundary_values_without_seeding(
    bridge, live11_automation
):
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
                    {
                        "type": "read_automation",
                        "params": {**TARGET, "times": [0, 0.25, 0.2501, 1.0, 1.0001]},
                    },
                ]
            },
        }
    )
    assert reply["status"] == "success", reply
    results = reply["result"]["results"]
    assert results[0]["result"]["start_settled"] is None
    assert [sample["value"] for sample in results[1]["result"]["samples"]] == pytest.approx(
        [0.25, 0.25, 0.2, 0.6, 0.8]
    )
    assert len(live11_automation.created) == 1
    assert live11_automation.created[0].baseline == 0.25
    assert not any(event[0] == "seed" for event in live11_automation.events)
    assert bridge.edits == ["begin", "end"]


def test_fractional_read_grid_clamps_roundoff_to_the_clip_endpoint(bridge, automation):
    automation.clip.length = 0.3
    automation.clip.end_marker = 0.3
    automation.clip.loop_end = 0.3
    reply = dispatch(bridge, "read_automation", start=0, end=0.3, step=0.1)
    assert reply["status"] == "success", reply
    assert automation.envelope.reads == [0.0, 0.1, 0.2, 0.3]
    assert automation.events == [("lookup",)]


def test_terminal_ramp_jump_is_rejected_without_clearing_or_seeding(bridge, automation):
    original = list(automation.envelope.steps)
    reply = dispatch(
        bridge,
        "write_automation",
        mode="ramp",
        points=[
            {"time": 0, "value": 0.0},
            {"time": 4, "value": 0.5},
            {"time": 4, "value": 1.0},
        ],
    )
    assert reply["status"] == "error", reply
    assert automation.envelope.steps == original
    assert automation.parameter.value == 0.25
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)


@pytest.mark.parametrize("create_api", ["missing", "not_callable"])
def test_envelope_creation_support_is_required_before_clearing_an_existing_envelope(
    bridge, automation, create_api
):
    if create_api == "missing":
        del automation.clip.create_automation_envelope
    else:
        automation.clip.create_automation_envelope = None
    original = list(automation.envelope.steps)
    reply = dispatch(bridge, "write_automation", points=[{"time": 0, "value": 0.8}])
    assert reply["status"] == "error", reply
    assert automation.envelope.steps == original
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)


@pytest.mark.parametrize("insert_api", ["missing", "not_callable"])
def test_existing_envelope_write_support_is_checked_before_clear(bridge, automation, insert_api):
    if insert_api == "missing":
        automation.clip.envelope = SimpleNamespace(value_at_time=lambda _: 0.5)
    else:
        automation.envelope.insert_step = None
    original = list(automation.envelope.steps)
    reply = dispatch(bridge, "write_automation", points=[{"time": 0, "value": 0.8}])
    assert reply["status"] == "error", reply
    assert automation.envelope.steps == original
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)


@pytest.mark.parametrize("command", ["write_automation", "read_automation"])
def test_end_marker_controls_automation_bounds_even_if_other_lengths_are_larger(
    bridge, automation, command
):
    automation.clip.end_marker = 2.0
    automation.clip.loop_end = 4.0
    automation.clip.length = 8.0
    params = (
        {"points": [{"time": 2.1, "value": 0.8}]}
        if command == "write_automation"
        else {"times": [2.1]}
    )
    reply = dispatch(bridge, command, **params)
    assert reply["status"] == "error", reply
    assert automation.envelope.reads == []
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)


@pytest.mark.parametrize(
    "tool_name, payload",
    [
        ("write_automation", {"points": [{"time": True, "value": 0.5}]}),
        ("write_automation", {"points": [{"time": "0", "value": 0.5}]}),
        ("write_automation", {"points": [{"time": 0, "value": True}]}),
        ("write_automation", {"points": [{"time": 0, "value": "0.5"}]}),
        ("write_automation", {"points": [{"time": 0, "value": 0.5, "duration": True}]}),
        ("write_automation", {"points": [{"time": 0, "value": 0.5, "duration": "1"}]}),
        (
            "write_automation",
            {"points": [{"time": 0, "value": 0.5}], "step_length": True},
        ),
        (
            "write_automation",
            {"points": [{"time": 0, "value": 0.5}], "step_length": "0.25"},
        ),
        ("read_automation", {"times": [True]}),
        ("read_automation", {"times": ["0"]}),
        ("read_automation", {"start": True, "end": 2, "step": 0.25}),
        ("read_automation", {"start": "0", "end": 2, "step": 0.25}),
        ("read_automation", {"start": 0, "end": True, "step": 0.25}),
        ("read_automation", {"start": 0, "end": "2", "step": 0.25}),
        ("read_automation", {"start": 0, "end": 2, "step": True}),
        ("read_automation", {"start": 0, "end": 2, "step": "0.25"}),
    ],
)
def test_mcp_automation_rejects_bool_and_string_numbers_without_touching_live(
    bridge_connection, automation, monkeypatch, tool_name, payload
):
    bridge_connection.require_capability = lambda _: None
    monkeypatch.setattr(arrangement, "get_ableton_connection", lambda: bridge_connection)
    published = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert tool_name in published
    try:
        reply = asyncio.run(mcp.call_tool(tool_name, {**TARGET, **payload}))
    except Exception as exc:
        # Invalid public arguments may be rejected by FastMCP/Pydantic before
        # the worker wrapper can construct its structured error result.
        assert any(word in str(exc).lower() for word in ("validation", "number", "float", "int"))
    else:
        assert reply.isError is True, reply
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert automation.envelope.reads == []
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_standalone_beat_zero_write_uses_one_explicit_undo_group(bridge, automation, mode):
    reply = dispatch(
        bridge,
        "write_automation",
        mode=mode,
        points=[{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
    )
    assert reply["status"] == "success", reply
    assert bridge.edits == ["begin", "end"]


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_standalone_write_closes_undo_group_if_its_start_never_settles(
    bridge, bridge_module, automation, monkeypatch, mode
):
    def ignore_value_change(parameter, value):
        parameter.events.append(("seed", value))

    monkeypatch.setattr(
        automation.parameter.__class__,
        "value",
        property(lambda _: 0.25, ignore_value_change),
    )
    clock = [0.0]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    # Advance only the bridge's timeout clock; leave the dispatcher fixture's
    # real scheduling clock untouched and avoid a two-second wall-time wait.
    monkeypatch.setattr(bridge_module, "time", SimpleNamespace(monotonic=monotonic))
    reply = dispatch(
        bridge,
        "write_automation",
        mode=mode,
        points=[{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
    )
    assert reply["status"] == "error", reply
    assert bridge.edits == ["begin", "end"]
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert not any(event[0] in {"clear", "create", "insert"} for event in automation.events)


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_standalone_write_closes_undo_group_when_live_rejects_an_insert(bridge, automation, mode):
    def fail_insert(*args):
        raise RuntimeError("Live insert failed")

    automation.envelope.insert_step = fail_insert
    reply = dispatch(
        bridge,
        "write_automation",
        mode=mode,
        points=[{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
    )
    assert reply["status"] == "error", reply
    assert "Live insert failed" in reply["message"]
    assert bridge.edits == ["begin", "end"]


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_unavailable_optional_beat_zero_readback_warns_without_repeating_the_edit(
    bridge, automation, mode
):
    read_attempts = []

    def unavailable_readback(time):
        read_attempts.append(time)
        raise RuntimeError("Live readback is unavailable")

    automation.envelope.value_at_time = unavailable_readback
    reply = dispatch(
        bridge,
        "write_automation",
        mode=mode,
        points=[{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
    )
    assert reply["status"] == "success", reply
    assert reply["result"]["start_settled"] is None
    assert "readback" in reply["result"]["warning"].lower()
    assert read_attempts == [0.0]
    assert automation.events.count(("clear",)) == 1
    inserts = [event for event in automation.events if event[0] == "insert"]
    assert len(inserts) == len(automation.envelope.steps) > 0
    assert bridge.edits == ["begin", "end"]


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_late_write_tick_rejects_before_clear_even_if_the_seed_value_already_matches(
    bridge, bridge_module, automation, monkeypatch, mode
):
    clock = [0.0]
    monkeypatch.setattr(
        bridge_module,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    deferred = bridge._write_automation(
        0,
        0,
        0,
        "Gain",
        [{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
        mode=mode,
    )
    assert isinstance(deferred, bridge_module._Deferred)
    finished = []
    deferred.start(lambda result=None, error=None: finished.append((result, error)))
    assert automation.parameter.value == 0.2
    assert bridge.pending
    clock[0] = 3.0
    bridge.pending.pop(0)()
    assert len(finished) == 1 and finished[0][0] is None
    error = finished[0][1]
    assert isinstance(error, bridge_module.CommandFailure)
    assert error.details["envelope_replaced"] is False
    assert error.details["may_have_applied"] is True
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert not any(event[0] in {"clear", "create", "insert"} for event in automation.events)
    assert bridge.pending == []
    assert bridge.edits == ["begin", "end"]


@pytest.mark.parametrize("state", [1, 2], ids=["active", "overridden"])
@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_active_or_overridden_automation_rejects_beat_zero_writes_before_any_edit(
    bridge, automation, state, mode
):
    automation.parameter.automation_state = state
    reply = dispatch(
        bridge,
        "write_automation",
        mode=mode,
        points=[{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
    )
    assert reply["status"] == "error", reply
    assert automation.parameter.value == 0.25
    assert automation.parameter.automation_state == state
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)
    assert bridge.edits == []


@pytest.mark.parametrize("state", [1, 2], ids=["active", "overridden"])
@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_nonzero_writes_can_replace_active_or_overridden_automation_without_seeding(
    bridge, automation, state, mode
):
    automation.parameter.automation_state = state
    reply = dispatch(
        bridge,
        "write_automation",
        mode=mode,
        points=[{"time": 0.25, "value": 0.2}, {"time": 1.25, "value": 0.8}],
    )
    assert reply["status"] == "success", reply
    assert reply["result"]["prior_automation_state"] == state
    assert reply["result"]["start_value_set"] is None
    assert automation.parameter.value == 0.25
    assert not any(event[0] == "seed" for event in automation.events)
    assert automation.events.count(("clear",)) == 1
    assert bridge.edits == ["begin", "end"]


@pytest.mark.parametrize("state", [1, 2], ids=["active", "overridden"])
def test_active_or_overridden_automation_can_still_be_read(bridge, automation, state):
    automation.parameter.automation_state = state
    reply = dispatch(bridge, "read_automation", times=[0, 1])
    assert reply["status"] == "success", reply
    assert automation.envelope.reads == [0.0, 1.0]
    assert automation.parameter.automation_state == state
    assert automation.events == [("lookup",)]
    assert bridge.edits == []


@pytest.mark.parametrize("start", [0, 0.25])
@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_quantized_parameters_reject_fractional_points_or_any_ramp_before_edits(
    bridge, automation, start, mode
):
    automation.parameter.is_quantized = True
    final_value = 0.5 if mode == "points" else 1
    reply = dispatch(
        bridge,
        "write_automation",
        mode=mode,
        points=[{"time": start, "value": 0}, {"time": start + 1, "value": final_value}],
    )
    assert reply["status"] == "error", reply
    assert automation.parameter.value == 0.25
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)
    assert bridge.edits == []


def test_quantized_parameters_accept_native_integer_points(bridge, automation):
    automation.parameter.is_quantized = True
    automation.parameter._value = 0
    reply = dispatch(
        bridge,
        "write_automation",
        points=[{"time": 0, "value": 0}, {"time": 1, "value": 1}],
    )
    assert reply["status"] == "success", reply
    assert automation.envelope.steps == [(0.0, 1.0, 0.0), (1.0, 3.0, 1.0)]
    assert reply["result"]["prior_value"] == 0
    assert bridge.edits == ["begin", "end"]


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_batch_beat_zero_write_fails_preflight_before_other_commands(bridge, automation, mode):
    bridge._create_midi_track = lambda _: bridge.edits.append("created") or {"index": 0}
    reply = bridge.dispatch(
        {
            "type": "batch",
            "params": {
                "commands": [
                    {"type": "create_midi_track"},
                    {
                        "type": "write_automation",
                        "params": {
                            **TARGET,
                            "mode": mode,
                            "points": [{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
                        },
                    },
                    {"type": "set_tempo", "params": {"tempo": 90}},
                ]
            },
        }
    )
    assert reply["status"] == "error", reply
    assert reply["details"]["phase"] == "preflight"
    assert reply["details"]["failed_step"] == 2
    assert reply["details"]["completed_count"] == 0
    assert reply["details"]["may_have_applied"] is False
    assert bridge._song.tempo == 120
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert automation.events == []
    assert bridge.edits == []


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_command_reply_deadline_bounds_deferred_automation_and_retains_its_failure(
    bridge, bridge_module, automation, monkeypatch, mode
):
    clock = [0.0]
    monkeypatch.setattr(bridge_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    bridge._COMMAND_TIMEOUTS = {"write_automation": 1.0}
    schedule = bridge.schedule_message

    def schedule_message(delay, callback):
        def after_live_update():
            if delay > 0:
                # The command has one second total. This callback resumes
                # before the local two-second limit but after its reply budget.
                clock[0] = 1.1
            callback()

        schedule(delay, after_live_update)

    bridge.schedule_message = schedule_message
    operation_id = "reply-deadline-after-seed-" + mode
    reply = bridge.dispatch(
        {
            "type": "write_automation",
            "operation_id": operation_id,
            "params": {
                **TARGET,
                "mode": mode,
                "points": [{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
            },
        }
    )
    assert reply["status"] == "error", reply
    assert reply["details"]["may_have_applied"] is True
    assert reply["details"]["envelope_replaced"] is False
    assert automation.parameter.value == 0.2
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert not any(event[0] in {"clear", "create", "insert"} for event in automation.events)
    assert bridge.edits == ["begin", "end"]
    assert bridge.pending == []
    receipt = bridge._get_operation_status(operation_id)
    assert receipt["state"] == "failed"
    assert receipt["response"] == reply
    assert getattr(bridge, "_command_deadline", None) is None


@pytest.mark.parametrize("mode", ["points", "ramp"])
def test_expired_command_context_rejects_before_seed_and_retains_a_no_edit_failure(
    bridge, bridge_module, automation, monkeypatch, mode
):
    clock = [0.0]
    monkeypatch.setattr(bridge_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    bridge._COMMAND_TIMEOUTS = {"write_automation": 1.0}
    schedule = bridge.schedule_message

    def schedule_message(delay, callback):
        def after_queue_delay():
            clock[0] = 1.1
            callback()

        schedule(delay, after_queue_delay)

    bridge.schedule_message = schedule_message
    operation_id = "reply-deadline-before-seed-" + mode
    reply = bridge.dispatch(
        {
            "type": "write_automation",
            "operation_id": operation_id,
            "params": {
                **TARGET,
                "mode": mode,
                "points": [{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}],
            },
        }
    )
    assert reply["status"] == "error", reply
    assert reply["details"]["may_have_applied"] is False
    assert reply["details"]["envelope_replaced"] is False
    assert automation.parameter.value == 0.25
    assert automation.envelope.steps == [(0.0, 4.0, 0.5)]
    assert not any(event[0] in {"clear", "create", "insert", "seed"} for event in automation.events)
    assert bridge.edits == []
    assert bridge.pending == []
    receipt = bridge._get_operation_status(operation_id)
    assert receipt["state"] == "failed"
    assert receipt["response"] == reply
    assert getattr(bridge, "_command_deadline", None) is None
