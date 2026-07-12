"""Unit tests for the pure key-detection function `detect_key`.

These exercise the Krumhansl-Kessler math only: no Live, no socket. Notes carry
our standard {pitch, start_time, duration, velocity, mute} shape.
"""

from ableton_live_mcp.tools.keys import detect_key

MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]  # semitone steps of a major scale from its tonic
NATURAL_MINOR = [0, 2, 3, 5, 7, 8, 10]  # semitone steps of a natural minor scale


def _scale_notes(tonic, steps, duration=1.0, velocity=100):
    """Build a full ascending octave run: one note per scale degree plus the
    octave tonic on top. Spanning the octave means the tonic sounds at both
    ends, the natural emphasis that separates a minor key from its relative
    major (both share the same seven pitch classes)."""
    pitches = [tonic + s for s in steps] + [tonic + 12]
    return [
        {
            "pitch": p,
            "start_time": float(i),
            "duration": duration,
            "velocity": velocity,
            "mute": False,
        }
        for i, p in enumerate(pitches)
    ]


def test_c_major_scale_detects_c_major():
    result = detect_key(_scale_notes(60, MAJOR_SCALE))
    assert result["key"] == "C major"
    assert result["tonic"] == "C"
    assert result["mode"] == "major"


def test_a_natural_minor_scale_detects_a_minor():
    # A natural minor from A3 (pitch 57): the classic relative-of-C case that
    # the minor profile must resolve correctly.
    result = detect_key(_scale_notes(57, NATURAL_MINOR))
    assert result["key"] == "A minor"
    assert result["tonic"] == "A"
    assert result["mode"] == "minor"


def test_c_major_triad_held_long_detects_c_major():
    triad = [
        {"pitch": 60, "start_time": 0.0, "duration": 8.0, "velocity": 100, "mute": False},
        {"pitch": 64, "start_time": 0.0, "duration": 8.0, "velocity": 100, "mute": False},
        {"pitch": 67, "start_time": 0.0, "duration": 8.0, "velocity": 100, "mute": False},
    ]
    result = detect_key(triad)
    assert result["key"] == "C major"


def test_empty_notes_returns_no_key_without_crashing():
    result = detect_key([])
    assert result["key"] is None
    assert result["tonic"] is None
    assert result["mode"] is None
    assert result["confidence"] == 0.0
    assert result["margin"] == 0.0
    assert result["runner_up"] is None
    assert "message" in result


def test_confidence_in_range_and_margin_nonnegative():
    result = detect_key(_scale_notes(60, MAJOR_SCALE))
    assert -1.0 <= result["confidence"] <= 1.0
    assert result["margin"] >= 0.0


def test_all_weightings_agree_on_a_clean_scale():
    # A flat, evenly-weighted scale should land on the same tonic regardless of
    # the weighting knob.
    for weight in ("duration", "velocity", "product", "count"):
        assert detect_key(_scale_notes(65, MAJOR_SCALE), weight=weight)["key"] == "F major"


def test_invalid_weight_raises():
    import pytest

    with pytest.raises(ValueError):
        detect_key(_scale_notes(60, MAJOR_SCALE), weight="loudness")
