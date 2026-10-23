"""Behaviour of write/clear_arrangement_automation in the Remote Script, on fake Live objects.

The Remote Script imports Live's `_Framework`, so the functions and methods under
test are compiled straight out of its AST into a stand-in class.
"""

import ast
import math
import re
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "ableton_live_mcp/remote_script/__init__.py"
MODULE_FUNCS = {
    "_display_number",
    "_sampled_display",
    "_parameter_value",
    "_automation_points",
    "_automation_value_at",
    "_automation_segments",
}
METHODS = {
    "_req",
    "_clip_time_range",
    "_resolve_automation_curves",
    "_write_carrier_envelope",
    "_write_arrangement_automation",
    "_clear_arrangement_automation",
}


def _load():
    tree = ast.parse(SCRIPT_PATH.read_text())
    namespace = {"math": math, "re": re}
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in MODULE_FUNCS]
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AbletonMCP")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    assert {f.name for f in funcs} == MODULE_FUNCS
    assert {m.name for m in methods} == METHODS
    module = ast.Module(body=funcs + methods, type_ignores=[])
    exec(compile(module, str(SCRIPT_PATH), "exec"), namespace)
    return namespace


NS = _load()


def utility_db(value):
    """Utility-style gain display: the bottom of the range reads '-inf dB'."""
    return "-inf dB" if value <= -1.0 else f"{value * 35:.2f} dB"


class Param:
    def __init__(self, name, lo=0.0, hi=1.0, display=None):
        self.name, self.min, self.max = name, lo, hi
        self.is_quantized = False
        self._display = display or (lambda v: f"{v * 100:.1f} %")

    def str_for_value(self, value):
        return self._display(value)


class Device:
    def __init__(self, name, params):
        self.name, self.parameters = name, params


class Envelope:
    def __init__(self):
        self.steps = []

    def insert_step(self, time, duration, value):
        self.steps.append((time, duration, value))

    def value_at(self, t):
        for start, duration, value in self.steps:
            if start <= t < start + duration:
                return value
        raise AssertionError(f"no step covers {t}")


class Clip:
    def __init__(
        self,
        name,
        start=0.0,
        length=16.0,
        midi=True,
        notes=None,
        looping=True,
        loop=None,
        start_marker=None,
    ):
        self.name, self.color = name, 7
        self.start_time, self.end_time = start, start + length
        self.is_midi_clip = midi
        self.looping = looping
        self.loop_start, self.loop_end = loop or (0.0, length)
        self.start_marker = self.loop_start if start_marker is None else start_marker
        self.end_marker = self.loop_end
        self.notes = notes or []
        self.envelopes = {}

    def clear_envelope(self, param):
        self.envelopes.pop(param.name, None)

    def automation_envelope(self, param):
        return self.envelopes.get(param.name)

    def create_automation_envelope(self, param):
        return self.envelopes.setdefault(param.name, Envelope())


class Slot:
    def __init__(self, clip=None):
        self.clip = clip

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        self.clip = Clip("", length=length)

    def delete_clip(self):
        self.clip = None


class Track:
    def __init__(self, clips, slots=2, filled_slots=0):
        self.name = "Seq"
        self.arrangement_clips = list(clips)
        self.clip_slots = [Slot(Clip("x") if i < filled_slots else None) for i in range(slots)]
        self.devices = [
            Device("Utility", [Param("Gain", -1.0, 1.0, utility_db)]),
            Device("Auto Filter", [Param("Frequency"), Param("Resonance")]),
        ]
        self.placed = []

    def duplicate_clip_to_arrangement(self, clip, time):
        copy = Clip(
            clip.name, start=time, length=clip.loop_end - clip.loop_start, notes=list(clip.notes)
        )
        copy.color, copy.envelopes = clip.color, dict(clip.envelopes)
        self.arrangement_clips = [c for c in self.arrangement_clips if c.start_time != time]
        self.arrangement_clips.append(copy)
        self.arrangement_clips.sort(key=lambda c: c.start_time)
        self.placed.append(copy)


class Song:
    def __init__(self):
        self.undo_depth, self.undo_steps = 0, 0
        self.view = type("View", (), {"detail_clip": None})()

    def begin_undo_step(self):
        self.undo_depth += 1
        self.undo_steps += 1

    def end_undo_step(self):
        self.undo_depth -= 1


def bridge(track):
    attrs = {name: NS[name] for name in METHODS}

    def _get_track(self, index):
        assert index == 0
        return track

    def _get_device(self, track_index, device_index):
        return track.devices[device_index]

    def _resolve_parameter(self, device, parameter):
        if isinstance(parameter, int):
            return device.parameters[parameter]
        return next(p for p in device.parameters if p.name == parameter)

    def _read_all_notes(self, clip):
        return [dict(n) for n in clip.notes]

    def _replace_all_notes(self, clip, notes):
        clip.notes = [dict(n) for n in notes]

    attrs.update(
        _get_track=_get_track,
        _get_device=_get_device,
        _resolve_parameter=_resolve_parameter,
        _read_all_notes=_read_all_notes,
        _replace_all_notes=_replace_all_notes,
    )
    obj = type("Bridge", (), attrs)()
    obj._song = Song()
    return obj


def ramp(param, t0, v0, t1, v1, device_index=1):
    return {
        "device_index": device_index,
        "parameter": param,
        "points": [{"time": t0, "value": v0}, {"time": t1, "value": v1}],
    }


def test_segments_ramp_steps_merge_and_clamp():
    seg = NS["_automation_segments"]
    assert seg([(0.0, 0.0, False), (4.0, 1.0, False)], 0.0, 4.0, 1.0, 0.0, 1.0) == [
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.25),
        (2.0, 1.0, 0.5),
        (3.0, 1.0, 0.75),
    ]
    # A step point holds until the next point; equal neighbours merge into one step.
    assert seg([(0.0, 0.0, True), (2.0, 1.0, False)], 0.0, 4.0, 1.0, 0.0, 1.0) == [
        (0.0, 2.0, 0.0),
        (2.0, 2.0, 1.0),
    ]
    # Values are clamped to the parameter range.
    assert seg([(0.0, 5.0, False)], 0.0, 2.0, 1.0, 0.0, 1.0) == [(0.0, 2.0, 1.0)]


def test_rebuilds_overlapping_midi_clips_and_skips_the_rest():
    notes = [{"pitch": 53, "start_time": 0.5, "duration": 0.25, "velocity": 100, "mute": False}]
    track = Track(
        [
            Clip("A", start=0, notes=notes),
            Clip("B", start=16, notes=notes),
            Clip("audio", start=32, midi=False),
            Clip("trimmed", start=48, length=32, loop=(0.0, 16.0)),
            Clip("far", start=96),
        ]
    )
    b = bridge(track)
    result = b._write_arrangement_automation(0, [ramp("Frequency", 8, 0.0, 72, 1.0)], 1.0)

    assert [c["name"] for c in result["rebuilt_clips"]] == ["A", "B"]
    assert {s["name"] for s in result["skipped"]} == {"audio", "trimmed"}
    placed = {c.name: c for c in track.placed}
    # Clip B starts at beat 16: its local beat 0 carries the ramp value at beat 16.
    env_b = placed["B"].envelopes["Frequency"]
    assert env_b.value_at(0.0) == pytest.approx((16 - 8) / 64)
    assert env_b.value_at(15.5) == pytest.approx((31 - 8) / 64)
    # Before the first point the start value is held.
    assert placed["A"].envelopes["Frequency"].value_at(2.0) == 0.0
    assert placed["B"].notes == notes and placed["B"].color == 7
    assert "far" not in placed
    # The carrier slot is emptied and the whole call is one undo step.
    assert not any(s.has_clip for s in track.clip_slots)
    assert b._song.undo_steps == 1 and b._song.undo_depth == 0


def test_extra_curves_go_only_into_clips_their_span_overlaps():
    track = Track([Clip("A", start=0), Clip("B", start=16)])
    b = bridge(track)
    b._write_arrangement_automation(
        0,
        [
            ramp("Frequency", 0, 0.0, 8, 1.0),
            ramp("Resonance", 20, 0.2, 24, 0.6),
        ],
        1.0,
    )
    placed = {c.name: c for c in track.placed}
    assert set(placed["A"].envelopes) == {"Frequency"}
    assert set(placed["B"].envelopes) == {"Resonance"}


def test_display_values_resolve_per_parameter():
    track = Track([Clip("A", start=0, length=4)])
    b = bridge(track)
    b._write_arrangement_automation(
        0,
        [
            {
                "device_index": 0,
                "parameter": "Gain",
                "points": [{"time": 0, "value": "-7 dB", "step": True}, {"time": 2, "value": 0.0}],
            }
        ],
        1.0,
    )
    env = track.placed[0].envelopes["Gain"]
    assert env.value_at(0.5) == pytest.approx(-0.2, abs=1e-3)
    assert env.value_at(3.0) == 0.0


def test_needs_an_empty_session_slot():
    track = Track([Clip("A", start=0)], slots=1, filled_slots=1)
    b = bridge(track)
    with pytest.raises(Exception, match="empty Session slot"):
        b._write_arrangement_automation(0, [ramp("Frequency", 0, 0.0, 8, 1.0)], 1.0)
    assert track.placed == []


def test_same_parameter_twice_is_rejected():
    b = bridge(Track([Clip("A")]))
    with pytest.raises(ValueError, match="appears twice"):
        b._write_arrangement_automation(
            0, [ramp("Frequency", 0, 0, 4, 1), ramp(0, 4, 1, 8, 0)], 1.0
        )


def test_clear_flattens_every_clip_to_the_end_of_the_track():
    track = Track([Clip("A", start=0), Clip("B", start=16)])
    b = bridge(track)
    result = b._clear_arrangement_automation(0, 1, "Frequency", 0.5)
    assert result["flattened_to"] == 0.5
    for clip in track.placed:
        env = clip.envelopes["Frequency"]
        assert env.steps == [(0.0, 16.0, 0.5)]
    assert [c["name"] for c in result["rebuilt_clips"]] == ["A", "B"]
