"""Unit tests for the pure generator functions in generators_advanced.

These exercise the music-theory algorithms only: no Live, no socket, everything
seeded and deterministic.
"""

import random

from ableton_live_mcp.tools.generators_advanced import (
    chord_voicing,
    drum_groove,
    euclidean,
    humanize,
    melody_line,
    parse_chord_symbol,
    progression_for_genre,
    scale_pitches,
    voice_progression,
    walking_bass,
)

REQUIRED_KEYS = {"pitch", "start_time", "duration", "velocity", "mute"}
TRESILLO = [1, 0, 0, 1, 0, 0, 1, 0]
CINQUILLO = [1, 0, 1, 1, 0, 1, 1, 0]


def _assert_valid_notes(notes):
    assert notes, "generator produced no notes"
    for n in notes:
        assert REQUIRED_KEYS <= set(n.keys()), f"missing keys: {REQUIRED_KEYS - set(n)}"
        assert 0 <= n["pitch"] <= 127
        assert 1 <= n["velocity"] <= 127
        assert n["start_time"] >= 0.0
        assert n["duration"] > 0.0
        assert isinstance(n["pitch"], int)
        assert isinstance(n["velocity"], int)


# ── Euclidean ────────────────────────────────────────────────────────


def test_euclidean_tresillo():
    assert euclidean(3, 8) == TRESILLO


def test_euclidean_cinquillo():
    # Bjorklund (unlike naive Bresenham) yields the true maximally-even necklace.
    assert euclidean(5, 8) == CINQUILLO


def test_euclidean_length_and_pulse_count():
    for pulses, steps in [(4, 16), (5, 13), (7, 16), (3, 7), (2, 5), (9, 16)]:
        pat = euclidean(pulses, steps)
        assert len(pat) == steps
        assert sum(pat) == pulses
        assert set(pat) <= {0, 1}
        assert pat[0] == 1  # canonical necklace starts on a hit


def test_euclidean_rotate():
    base = euclidean(3, 8)
    assert euclidean(3, 8, rotate=1) == base[1:] + base[:1]
    assert euclidean(3, 8, rotate=8) == base  # full rotation is identity


def test_euclidean_edges():
    assert euclidean(0, 4) == [0, 0, 0, 0]
    assert euclidean(4, 4) == [1, 1, 1, 1]
    assert euclidean(9, 4) == [1, 1, 1, 1]  # pulses clamped to steps


# ── Voicings ─────────────────────────────────────────────────────────


def test_rootless_voicing_has_third_and_seventh_no_root():
    root_pc, quality, _ = parse_chord_symbol("Dm7")
    pcs = {p % 12 for p in chord_voicing(root_pc, quality, style="rootless", voices=4)}
    assert (root_pc + 3) % 12 in pcs  # minor 3rd (F)
    assert (root_pc + 10) % 12 in pcs  # minor 7th (C)
    assert root_pc % 12 not in pcs  # rootless drops the root


def test_shell_voicing_is_root_third_seventh():
    root_pc, quality, _ = parse_chord_symbol("G7")
    pcs = {p % 12 for p in chord_voicing(root_pc, quality, style="shell", voices=3)}
    assert pcs == {root_pc % 12, (root_pc + 4) % 12, (root_pc + 10) % 12}


def test_quartal_voicing_stacks_perfect_fourths():
    root_pc, quality, _ = parse_chord_symbol("Am7")
    v = chord_voicing(root_pc, quality, style="quartal", voices=4)
    assert [b - a for a, b in zip(v, v[1:])] == [5, 5, 5]


def test_voicings_are_valid_midi():
    for sym in ["Cmaj7", "Am9", "F#m7b5", "Bb13", "G7"]:
        root_pc, quality, _ = parse_chord_symbol(sym)
        for style in ("rootless", "quartal", "shell", "block"):
            for p in chord_voicing(root_pc, quality, style=style):
                assert 0 <= p <= 127


# ── Generators produce valid notes ───────────────────────────────────


def test_all_generators_produce_valid_notes():
    prog = ["Am7", "Dm7", "G7", "Cmaj7"]
    _assert_valid_notes(voice_progression(prog, seed=1))
    _assert_valid_notes(melody_line(prog, key="A", scale="minor", seed=2))
    _assert_valid_notes(walking_bass(prog, key="A", scale="minor", seed=3))
    _assert_valid_notes(drum_groove(bars=2, style="lofi", seed=4))
    _assert_valid_notes(drum_groove(bars=1, style="trap", seed=5))


def test_generators_are_reproducible():
    prog = ["Am7", "Dm7", "G7", "Cmaj7"]
    assert melody_line(prog, seed=11) == melody_line(prog, seed=11)
    assert walking_bass(prog, seed=11) == walking_bass(prog, seed=11)
    assert drum_groove(seed=11) == drum_groove(seed=11)
    # Different seeds should (almost surely) diverge.
    assert melody_line(prog, seed=1) != melody_line(prog, seed=2)


# ── Humanize ─────────────────────────────────────────────────────────


def test_humanize_reproducible_and_preserves_pitch():
    base = drum_groove(bars=2, seed=7)
    a = humanize(base, timing=0.03, velocity=10, seed=42)
    b = humanize(base, timing=0.03, velocity=10, seed=42)
    assert a == b  # deterministic for a fixed seed
    assert [n["pitch"] for n in a] == [n["pitch"] for n in base]  # pitches untouched
    assert a is not base and a[0] is not base[0]  # new objects, input not mutated


def test_humanize_keeps_notes_in_range():
    # Push velocities to the extremes so clamping is actually exercised.
    edge = [
        {"pitch": 60, "start_time": 0.0, "duration": 0.5, "velocity": 1, "mute": False},
        {"pitch": 127, "start_time": 0.1, "duration": 0.5, "velocity": 127, "mute": False},
    ]
    out = humanize(edge, timing=0.5, velocity=40, seed=3)
    for n in out:
        assert 1 <= n["velocity"] <= 127
        assert 0 <= n["pitch"] <= 127
        assert n["start_time"] >= 0.0  # timing jitter never goes negative


def test_humanize_different_seeds_differ():
    base = melody_line(["Am7", "Dm7"], seed=1)
    assert humanize(base, seed=1) != humanize(base, seed=2)


# ── Voice-leading minimizes leaps ────────────────────────────────────


def _mean_abs_leap(notes):
    pitches = [n["pitch"] for n in notes]
    if len(pitches) < 2:
        return 0.0
    return sum(abs(b - a) for a, b in zip(pitches, pitches[1:])) / (len(pitches) - 1)


def test_voice_leading_reduces_average_leap():
    prog = ["Am7", "Dm7", "G7", "Cmaj7", "Fmaj7", "Bm7b5", "E7", "Am7"]
    pool = scale_pitches("A", "minor", 60, 84)
    vl_total = 0.0
    naive_total = 0.0
    trials = 20
    for seed in range(trials):
        vl = melody_line(prog, key="A", scale="minor", seed=seed, voice_leading=True)
        # A genuinely naive line: random scale tones over the same register.
        rng = random.Random(seed)
        naive = [{"pitch": rng.choice(pool)} for _ in vl]
        vl_total += _mean_abs_leap(vl)
        naive_total += _mean_abs_leap(naive)
    assert vl_total / trials < naive_total / trials


# ── Genre progressions ───────────────────────────────────────────────


def test_progression_for_genre_transposes_and_parses():
    chords = progression_for_genre("lofi", key="C", scale="major", bars=4)
    assert len(chords) == 4
    assert chords[0] == "Cmaj7"  # degree 0, maj7 quality, in C major
    for c in chords:
        parse_chord_symbol(c)  # every symbol must be parseable


def test_progression_for_genre_loops_to_requested_bars():
    chords = progression_for_genre("jazz", key="A", scale="minor", bars=8)
    assert len(chords) == 8
    assert chords[:4] == chords[4:8]  # 4-chord grammar repeats


def test_progression_fuzzy_genre_match():
    # "deep house" (spaces) should resolve to the deep_house grammar.
    assert progression_for_genre("deep house", key="C", scale="minor", bars=4)
