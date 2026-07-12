"""Unit tests for the pure motif-transformation functions in tools.motif.

These exercise the note-list algorithms only: no Live, no socket. Every function
is pure (returns a new list, never mutates its input) and deterministic.
"""

import copy

from ableton_live_mcp.tools.motif import (
    additive,
    augment,
    invert,
    phase_pattern,
    retrograde,
    transpose,
)

REQUIRED_KEYS = {"pitch", "start_time", "duration", "velocity", "mute"}


def _note(pitch, start, dur, vel=90, mute=False):
    return {
        "pitch": pitch,
        "start_time": start,
        "duration": dur,
        "velocity": vel,
        "mute": mute,
    }


def _motif():
    """A simple four-note ascending motif, one beat apart, non-overlapping."""
    return [
        _note(60, 0.0, 1.0, 80),
        _note(62, 1.0, 1.0, 90),
        _note(64, 2.0, 1.0, 100),
        _note(67, 3.0, 1.0, 110),
    ]


def _assert_valid_notes(notes):
    for n in notes:
        assert REQUIRED_KEYS <= set(n.keys()), f"missing keys: {REQUIRED_KEYS - set(n)}"
        assert isinstance(n["pitch"], int)
        assert isinstance(n["velocity"], int)
        assert 0 <= n["pitch"] <= 127
        assert 1 <= n["velocity"] <= 127
        assert n["start_time"] >= 0.0
        assert n["duration"] >= 0.0


# ── purity ────────────────────────────────────────────────────────────


def test_functions_do_not_mutate_input():
    src = _motif()
    snapshot = copy.deepcopy(src)
    transpose(src, 5)
    invert(src)
    retrograde(src)
    augment(src, 2.0)
    phase_pattern(src, 3, 0.25)
    additive(src, 4)
    assert src == snapshot  # every operator left the input untouched


# ── transpose ─────────────────────────────────────────────────────────


def test_transpose_up_octave_shifts_all_pitches_by_12():
    src = _motif()
    out = transpose(src, 12)
    assert [n["pitch"] for n in out] == [p["pitch"] + 12 for p in src]
    _assert_valid_notes(out)


def test_transpose_clamps_out_of_range_pitches():
    out = transpose([_note(120, 0.0, 1.0), _note(2, 0.0, 1.0)], 12)
    assert out[0]["pitch"] == 127  # 132 clamped down
    out_down = transpose([_note(2, 0.0, 1.0)], -12)
    assert out_down[0]["pitch"] == 0  # -10 clamped up
    _assert_valid_notes(out)


def test_transpose_preserves_timing():
    src = _motif()
    out = transpose(src, 3)
    assert [n["start_time"] for n in out] == [p["start_time"] for p in src]
    assert [n["duration"] for n in out] == [p["duration"] for p in src]


# ── invert ────────────────────────────────────────────────────────────


def test_invert_mirrors_about_axis():
    axis = 64
    src = _motif()
    out = invert(src, axis_pitch=axis)
    for orig, got in zip(src, out):
        assert got["pitch"] == 2 * axis - orig["pitch"]
    _assert_valid_notes(out)


def test_invert_default_axis_is_first_note_and_fixes_it():
    src = _motif()
    out = invert(src)  # default axis = first note's pitch (60)
    assert out[0]["pitch"] == src[0]["pitch"]  # axis note is unmoved
    assert out[1]["pitch"] == 2 * 60 - 62  # 58


# ── retrograde ────────────────────────────────────────────────────────


def test_retrograde_reverses_onset_order():
    src = _motif()
    out = retrograde(src)
    onsets = [n["start_time"] for n in out]
    # the first input note (earliest onset) now has the latest onset, and the
    # last input note now starts first -- i.e. emitted in reverse onset order
    assert onsets == sorted(onsets, reverse=True)
    assert out[0]["start_time"] > out[-1]["start_time"]
    assert min(onsets) == 0.0  # last-ending note now starts at 0
    # sorting the retrograde onsets ascending reverses the original index order
    order = sorted(range(len(out)), key=lambda i: out[i]["start_time"])
    assert order == list(reversed(range(len(src))))


def test_retrograde_preserves_duration_set():
    src = _motif()
    out = retrograde(src)
    assert sorted(n["duration"] for n in out) == sorted(p["duration"] for p in src)
    _assert_valid_notes(out)


def test_retrograde_mirrors_uneven_durations():
    src = [_note(60, 0.0, 0.5), _note(62, 0.5, 1.5), _note(64, 2.0, 1.0)]  # total end = 3.0
    out = retrograde(src)
    # note at [0.0,0.5] -> [3.0-0.5, 3.0-0.0] = [2.5, 3.0]
    assert out[0]["start_time"] == 2.5 and out[0]["duration"] == 0.5
    # note at [2.0,3.0] -> [0.0, 1.0]
    assert out[2]["start_time"] == 0.0 and out[2]["duration"] == 1.0


# ── augment ───────────────────────────────────────────────────────────


def test_augment_double_scales_starts_and_durations():
    src = _motif()
    out = augment(src, 2.0)
    for orig, got in zip(src, out):
        assert got["start_time"] == orig["start_time"] * 2.0
        assert got["duration"] == orig["duration"] * 2.0
        assert got["pitch"] == orig["pitch"]  # pitch untouched
    _assert_valid_notes(out)


def test_augment_compresses_when_factor_below_one():
    src = _motif()
    out = augment(src, 0.5)
    for orig, got in zip(src, out):
        assert got["start_time"] == orig["start_time"] * 0.5
        assert got["duration"] == orig["duration"] * 0.5


# ── phase_pattern ─────────────────────────────────────────────────────


def test_phase_pattern_triples_note_count():
    src = _motif()
    out = phase_pattern(src, repeats=3, shift=0.25)
    assert len(out) == 3 * len(src)
    _assert_valid_notes(out)


def test_phase_pattern_shifts_each_copy_later():
    src = _motif()
    out = phase_pattern(src, repeats=3, shift=0.25)
    n = len(src)
    # copy 0 unshifted, copy 1 +0.25, copy 2 +0.50 relative to the source onsets
    for k in range(3):
        for j, orig in enumerate(src):
            assert out[k * n + j]["start_time"] == orig["start_time"] + k * 0.25


def test_phase_pattern_zero_repeats_is_empty():
    assert phase_pattern(_motif(), repeats=0, shift=0.25) == []


# ── additive ──────────────────────────────────────────────────────────


def test_additive_builds_up_triangular_count():
    src = _motif()
    out = additive(src, 4)
    # stages of 1 + 2 + 3 + 4 notes
    assert len(out) == 1 + 2 + 3 + 4
    _assert_valid_notes(out)


def test_additive_caps_steps_at_length():
    src = _motif()  # 4 notes
    out = additive(src, 99)
    assert len(out) == 1 + 2 + 3 + 4  # capped at 4 stages


def test_additive_first_stage_is_first_note():
    src = _motif()
    out = additive(src, 4)
    assert out[0]["pitch"] == src[0]["pitch"]
    assert out[0]["start_time"] == 0.0
