"""Advanced generative music tools: Euclidean rhythm, jazz voicings, voice-led
melody, walking bass, pocketed drum grooves, humanization, and genre-aware
chord progressions.

Everything musical is computed here in pure Python. The generator functions take
a ``seed`` and use a *local* ``random.Random`` instance (never the global RNG),
so they are deterministic and unit-testable with no running Live. The thin
``@mcp.tool`` wrappers call those pure functions and ship the resulting note data
to Live over the same ``create_clip`` / ``add_notes_to_clip`` wire commands the
rest of the generators use, so they carry zero Live-API risk.

Notes use the project's standard dict shape, identical to ``generators.py``:
``{"pitch": int 0-127, "start_time": float beats, "duration": float beats,
"velocity": int 1-127, "mute": bool}``. Pitches and velocities are clamped to
valid MIDI ranges and start times are kept non-negative.
"""

import random

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from .generators import CHORD_QUALITIES, DRUM_MAP, NOTE_NAMES, _split_progression, _write_clip

# ── music-theory tables ──────────────────────────────────────────────
# NOTE_NAMES and CHORD_QUALITIES are reused from generators.py. generators.py
# ships no scale table, so scales live here; pitch-class spellings (sharps) are
# used when naming generated chord roots.

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],  # natural minor
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor": [0, 2, 3, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "major_pentatonic": [0, 2, 4, 7, 9],
    "blues": [0, 3, 5, 6, 7, 10],
    "bebop": [0, 2, 4, 5, 7, 8, 9, 11],  # major bebop (added b6)
    "chromatic": list(range(12)),
}

# Genre chord "grammars" as (scale_degree, chord_quality) pairs. Degrees are
# 0-based indices into the working scale; qualities are keys in CHORD_QUALITIES.
# progression_for_genre() transposes these into any key/scale.
GENRE_PROGRESSIONS = {
    # hip-hop family
    "lofi": [(0, "maj7"), (5, "maj7"), (1, "m7"), (4, "7")],
    "boom_bap": [(0, "m7"), (3, "m7"), (4, "7"), (0, "m7")],
    "hip_hop": [(0, "m7"), (6, "maj7"), (5, "maj7"), (4, "7")],
    "trap": [(0, "m"), (5, ""), (6, ""), (0, "m")],
    "drill": [(0, "m"), (6, "dim"), (6, ""), (0, "m")],
    "phonk": [(0, "m"), (5, ""), (0, "m"), (4, "7")],
    "cloud_rap": [(0, "maj7"), (5, "maj7"), (2, "maj7"), (4, "m7")],
    # house / techno / trance
    "house": [(0, "m"), (5, ""), (2, ""), (4, "")],
    "deep_house": [(0, "m7"), (3, "m7"), (4, "7"), (2, "maj7")],
    "afro_house": [(0, "m7"), (4, "m7"), (5, "maj7"), (6, "7")],
    "tech_house": [(0, "m7"), (6, ""), (0, "m7"), (4, "7")],
    "techno": [(0, "m"), (4, "m"), (3, "m"), (0, "m")],
    "melodic_techno": [(0, "m7"), (5, "maj7"), (2, "maj7"), (6, "7")],
    "trance": [(0, "m"), (5, ""), (2, ""), (6, "")],
    "progressive_trance": [(0, "m7"), (5, "maj7"), (2, "maj7"), (4, "m7")],
    # bass music
    "dnb": [(0, "m7"), (6, "maj7"), (5, "maj7"), (4, "7")],
    "liquid_dnb": [(0, "maj7"), (5, "maj7"), (3, "m7"), (6, "7")],
    "jungle": [(0, "m"), (6, ""), (5, ""), (4, "7")],
    "dubstep": [(0, "m"), (5, ""), (6, ""), (4, "7")],
    "future_garage": [(0, "m7"), (5, "maj7"), (3, "m7"), (4, "7")],
    # r&b / soul / funk
    "rnb": [(0, "m7"), (3, "m7"), (6, "7"), (2, "maj7")],
    "neo_soul": [(0, "m9"), (3, "m9"), (6, "7"), (2, "maj9")],
    "funk": [(0, "m7"), (3, "7"), (0, "m7"), (4, "7")],
    "soul": [(0, "m7"), (5, "maj7"), (3, "m7"), (4, "7")],
    "gospel": [(0, "maj7"), (3, "maj7"), (4, "7"), (0, "maj7")],
    "reggae": [(0, "m7"), (3, "m7"), (4, "7"), (0, "m7")],
    # jazz
    "jazz": [(1, "m7"), (4, "7"), (0, "maj7"), (3, "maj7")],
    "bossa_nova": [(1, "m7"), (4, "7"), (0, "maj7"), (4, "7")],
    "bebop": [(1, "m7"), (4, "7"), (0, "maj7"), (5, "7")],
    "modal_jazz": [(0, "m7"), (0, "m7"), (1, "m7"), (0, "m7")],
    # ambient / electronic / pop
    "ambient": [(0, "maj7"), (5, "maj7"), (2, "maj7"), (6, "maj7")],
    "downtempo": [(0, "m7"), (3, "m7"), (6, "7"), (5, "maj7")],
    "synthwave": [(0, "m"), (5, ""), (2, ""), (6, "")],
    "future_bass": [(0, "maj7"), (5, "maj7"), (2, "maj7"), (6, "7")],
    "pop": [(0, ""), (4, ""), (5, "m"), (3, "")],
    "cinematic": [(0, "m"), (5, ""), (2, ""), (6, "")],
    # global
    "afrobeats": [(0, "m7"), (5, "maj7"), (6, "7"), (4, "m7")],
    "amapiano": [(0, "m7"), (3, "m7"), (6, "7"), (2, "maj7")],
    "reggaeton": [(0, "m"), (5, ""), (6, ""), (4, "7")],
}


# ── note construction + parsing helpers ──────────────────────────────


def _clamp_pitch(p):
    return max(0, min(127, int(round(p))))


def _clamp_vel(v):
    return max(1, min(127, int(round(v))))


def _note(pitch, start, dur, vel, mute=False):
    """Build a note in the project's standard dict shape, clamped to valid MIDI.

    Pitch -> 0-127, velocity -> 1-127, start_time kept non-negative, duration
    kept non-negative.
    """
    return {
        "pitch": _clamp_pitch(pitch),
        "start_time": round(max(0.0, float(start)), 4),
        "duration": round(max(0.0, float(dur)), 4),
        "velocity": _clamp_vel(vel),
        "mute": bool(mute),
    }


def parse_chord_symbol(symbol):
    """'Am9' -> (9, 'm9', [0, 3, 7, 10, 14]); 'F#maj7' -> (6, 'maj7', ...).

    Returns (root_pitch_class, quality_suffix, intervals). Reuses generators.py's
    NOTE_NAMES and CHORD_QUALITIES so the vocabulary matches the rest of the tools.
    """
    s = symbol.strip()
    if not s:
        raise ValueError("empty chord symbol")
    root = s[0].upper()
    rest = s[1:]
    if rest[:1] in ("#", "b", "B"):
        root += "#" if rest[0] == "#" else "B"
        rest = rest[1:]
    if root not in NOTE_NAMES:
        raise ValueError(f"unknown chord root in '{symbol}'")
    if rest not in CHORD_QUALITIES:
        raise ValueError(f"unknown chord quality '{rest}' in '{symbol}'")
    return NOTE_NAMES[root], rest, CHORD_QUALITIES[rest]


def _parse_note_name(name):
    """Parse a bare note name ('A', 'Bb', 'F#') into a pitch class; ignores any
    trailing chord/scale text (so a key of 'Am' reads as 'A')."""
    s = name.strip()
    if not s:
        raise ValueError("empty note name")
    root = s[0].upper()
    if len(s) > 1 and s[1] in ("#", "b", "B"):
        root += "#" if s[1] == "#" else "B"
    if root not in NOTE_NAMES:
        raise ValueError(f"unknown note name '{name}'")
    return NOTE_NAMES[root]


def _scale_intervals(name):
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in SCALES:
        raise ValueError(f"unknown scale '{name}'. Known: {sorted(SCALES)}")
    return SCALES[key]


def scale_pitches(key, scale, low, high):
    """Every MIDI pitch of the given key+scale inside [low, high] inclusive."""
    root = _parse_note_name(key)
    intervals = _scale_intervals(scale)
    out = set()
    start_oct = (low // 12) - 1
    end_oct = (high // 12) + 1
    for octv in range(start_oct, end_oct + 1):
        base = octv * 12 + root
        for iv in intervals:
            p = base + iv
            if low <= p <= high:
                out.add(p)
    return sorted(out)


# ── 1. Euclidean rhythm (Bjorklund) ──────────────────────────────────


def _bjorklund(pulses, steps):
    """Bjorklund's algorithm: the maximally-even distribution of ``pulses`` hits
    across ``steps`` slots, rotated to start on a hit. Returns a list of 0/1."""
    counts = []
    remainders = [pulses]
    divisor = steps - pulses
    level = 0
    while True:
        counts.append(divisor // remainders[level])
        remainders.append(divisor % remainders[level])
        divisor = remainders[level]
        level += 1
        if remainders[level] <= 1:
            break
    counts.append(divisor)

    pattern = []

    def build(lvl):
        if lvl == -1:
            pattern.append(0)
        elif lvl == -2:
            pattern.append(1)
        else:
            for _ in range(counts[lvl]):
                build(lvl - 1)
            if remainders[lvl] != 0:
                build(lvl - 2)

    build(level)
    # Rotate so the sequence begins on its first hit (canonical necklace form).
    if 1 in pattern:
        i = pattern.index(1)
        pattern = pattern[i:] + pattern[:i]
    return pattern


def euclidean(pulses, steps, rotate=0):
    """Euclidean rhythm via Bjorklund: distribute ``pulses`` hits as evenly as
    possible across ``steps`` slots.

    Returns a list of length ``steps`` of 0 (rest) / 1 (hit). ``pulses`` is
    clamped to [0, steps]. ``rotate`` shifts the pattern left by that many steps
    (the pattern starts later in the bar). euclidean(3, 8) == the classic
    tresillo [1, 0, 0, 1, 0, 0, 1, 0]; euclidean(5, 8) == the cinquillo.
    """
    if steps <= 0:
        raise ValueError("steps must be positive")
    steps = int(steps)
    pulses = max(0, min(int(pulses), steps))
    if pulses == 0:
        pattern = [0] * steps
    elif pulses == steps:
        pattern = [1] * steps
    else:
        pattern = _bjorklund(pulses, steps)
    if rotate:
        r = int(rotate) % steps
        pattern = pattern[r:] + pattern[:r]
    return pattern


# ── 2. Jazz voicings ─────────────────────────────────────────────────


def _place(offsets, root_pc, center):
    """Place a chord (semitone offsets from its root pitch-class) so the root
    sits in the octave nearest ``center``. Returns sorted, de-duplicated pitches."""
    root = root_pc
    while root < center - 6:
        root += 12
    while root >= center + 6:
        root -= 12
    return sorted({_clamp_pitch(root + iv) for iv in offsets})


def chord_voicing(root_pc, quality, style="rootless", center=60, voices=4):
    """Voice a single chord and return sorted MIDI pitches.

    style:
      rootless - drop the root, stack 3rd, 7th, then 9th/5th/13th tensions
                 (Bill-Evans-style comping; a 7th is always implied).
      quartal  - stack perfect fourths from the root (McCoy-Tyner-style).
      shell    - root, 3rd, 7th only (minimal bebop guide-tone voicing).
      block    - close-position chord tones straight from the quality.
    center biases where the voicing sits (60 = C3 in Live's naming); voices caps
    the number of notes.
    """
    intervals = CHORD_QUALITIES.get(quality, CHORD_QUALITIES[""])
    third_iv = 3 if 3 in intervals else 4
    seventh_iv = 11 if 11 in intervals else 10
    fifth_iv = 6 if 6 in intervals else (8 if 8 in intervals else 7)

    voices = max(1, int(voices))
    if style == "rootless":
        offsets = [third_iv, seventh_iv]
        for extra in (14, fifth_iv, 21, seventh_iv + 12):  # 9th, 5th, 13th, octave 7th
            if len(offsets) >= voices:
                break
            offsets.append(extra)
    elif style == "quartal":
        offsets = [5 * i for i in range(voices)]  # perfect fourth = 5 semitones
    elif style == "shell":
        offsets = [0, third_iv, seventh_iv][:voices]
    else:  # block
        offsets = list(intervals[:voices])
    return _place(offsets[:voices], root_pc, center)


def _voice_lead(voicing, prev):
    """Octave-shift a whole voicing to sit as close as possible to the previous
    one (minimizes movement between chords)."""
    if not voicing or not prev:
        return voicing
    target = sum(prev) / len(prev)
    best, best_cost = voicing, None
    for shift in (-12, 0, 12):
        cand = [p + shift for p in voicing]
        if any(p < 0 or p > 127 for p in cand):
            continue
        cost = abs(sum(cand) / len(cand) - target)
        if best_cost is None or cost < best_cost:
            best, best_cost = cand, cost
    return sorted(best)


def voice_progression(
    chords,
    style="rootless",
    beats_per_chord=4.0,
    center=60,
    velocity=64,
    voices=4,
    voice_leading=True,
    humanize=0,
    seed=None,
):
    """Voice a chord progression (list of symbols) into notes, one block per slot.

    Applies smooth voice-leading between chords when ``voice_leading`` is set.
    ``humanize`` adds +/- that much random velocity jitter. Returns note dicts.
    """
    if beats_per_chord <= 0:
        raise ValueError("beats_per_chord must be positive")
    humanize = max(0, humanize)
    rng = random.Random(seed)
    notes = []
    prev = None
    for i, sym in enumerate(chords):
        root_pc, quality, _ = parse_chord_symbol(sym)
        voicing = chord_voicing(root_pc, quality, style, center, voices)
        if voice_leading and prev is not None:
            voicing = _voice_lead(voicing, prev)
        prev = voicing
        t0 = i * beats_per_chord
        for j, p in enumerate(voicing):
            v = velocity + (rng.randint(-humanize, humanize) if humanize else 0)
            notes.append(_note(p, t0 + j * 0.006, beats_per_chord * 0.95, v))
    return notes


# ── 3. Melody (voice-leading + chromatic approach + phrase arc) ───────

_DENSITY_STEP = {"low": 1.0, "medium": 0.5, "high": 0.25}


def _phrase_arc(arc, idx, n):
    """Return a 0..1 register preference for step ``idx`` of ``n`` shaping the
    melodic contour across the phrase."""
    if n <= 1:
        return 0.5
    x = idx / (n - 1)
    if arc == "rising":
        return 0.4 + 0.5 * x
    if arc == "ascend_descend":
        return 0.3 + 0.7 * (1.0 - abs(x - 0.5) * 2.0)
    if arc == "arch":
        return 0.2 + 0.8 * (1.0 - (abs(x - 0.5) * 2.0) ** 1.5)
    return 0.5  # static


def melody_line(
    chords,
    key="C",
    scale="minor",
    beats_per_chord=4.0,
    density="medium",
    low=60,
    high=84,
    phrase_arc="arch",
    chromatic=0.15,
    chord_tone_bias=0.6,
    rest=0.08,
    swing=0.5,
    vel_low=70,
    vel_high=110,
    voice_leading=True,
    seed=None,
):
    """Generate a melodic line over a chord progression.

    Notes are chosen from ``key``/``scale`` and biased toward chord tones on
    strong beats. With ``voice_leading`` on, each note is picked from the nearest
    candidates to the previous one (small stepwise motion). ``chromatic`` is the
    per-note probability of inserting a chromatic approach note a half-step before
    a leap; ``phrase_arc`` (rising/arch/ascend_descend/static) shapes the register
    contour across each chord; ``density`` (low/medium/high) sets note rate;
    ``swing`` (0.5 straight .. ~0.66) delays off-beats. Returns note dicts.
    """
    if beats_per_chord <= 0:
        raise ValueError("beats_per_chord must be positive")
    rng = random.Random(seed)
    step = _DENSITY_STEP.get(density, 0.5)
    scale_notes = scale_pitches(key, scale, low, high)
    if not scale_notes:
        return []
    notes = []
    prev = None
    span = max(1, high - low)
    for i, sym in enumerate(chords):
        root_pc, _, intervals = parse_chord_symbol(sym)
        chord_pcs = {(root_pc + iv) % 12 for iv in intervals}
        bar_start = i * beats_per_chord
        n_steps = max(1, int(round(beats_per_chord / step)))
        for s in range(n_steps):
            beat = s * step
            t = bar_start + beat
            if step <= 0.5 and s % 2 == 1:
                t += (swing - 0.5) * step
            if rng.random() < rest:
                continue
            on_beat = abs(beat - round(beat)) < 1e-6

            target = low + span * _phrase_arc(phrase_arc, s, n_steps)
            window = span * 0.3
            cands = [p for p in scale_notes if abs(p - target) <= window] or scale_notes
            if on_beat and rng.random() < chord_tone_bias:
                cands = [p for p in cands if p % 12 in chord_pcs] or cands

            if voice_leading and prev is not None:
                nearest = sorted(cands, key=lambda p: abs(p - prev))
                pitch = rng.choice(nearest[: max(1, min(3, len(nearest)))])
            else:
                pitch = rng.choice(cands)

            if (
                chromatic
                and prev is not None
                and rng.random() < chromatic
                and abs(pitch - prev) > 2
            ):
                direction = 1 if pitch > prev else -1
                approach = pitch - direction
                at = t - step * 0.5
                if low <= approach <= high and at >= 0:
                    notes.append(_note(approach, at, step * 0.45, (vel_low + vel_high) // 2))

            spread = vel_high - vel_low
            if on_beat:
                vel = vel_low + int(spread * (0.5 + rng.random() * 0.5))
            else:
                vel = vel_low + int(spread * (0.25 + rng.random() * 0.45))
            notes.append(_note(pitch, t, step * 0.9, vel))
            prev = pitch
    return notes


# ── 4. Walking bass ──────────────────────────────────────────────────


def _lowest_with_pc(low, high, pc):
    for p in range(low, high + 1):
        if p % 12 == pc:
            return p
    return low


def walking_bass(
    chords,
    key="C",
    scale="minor",
    beats_per_chord=4.0,
    low=36,
    high=55,
    ghost=0.15,
    velocity=90,
    seed=None,
):
    """Generate a quarter-note walking bass line under a chord progression.

    Each bar lands the root on beat 1, walks chord/scale tones through the middle
    beats, and steps chromatically into the *next* chord's root on the last beat.
    ``ghost`` is the per-offbeat probability of a soft ghost note; ``low``/``high``
    bound the register. Returns note dicts.
    """
    if beats_per_chord <= 0:
        raise ValueError("beats_per_chord must be positive")
    rng = random.Random(seed)
    beats = max(1, int(round(beats_per_chord)))
    base_scale = scale_pitches(key, scale, low, high)
    notes = []
    prev = None
    n = len(chords)
    for i, sym in enumerate(chords):
        root_pc, _, intervals = parse_chord_symbol(sym)
        chord_pcs = {(root_pc + iv) % 12 for iv in intervals}
        chord_pitches = [p for p in range(low, high + 1) if p % 12 in chord_pcs]
        scale_notes = base_scale or chord_pitches
        root_pitch = _lowest_with_pc(low, high, root_pc)
        next_root_pc, _, _ = parse_chord_symbol(chords[(i + 1) % n])
        next_root = _lowest_with_pc(low, high, next_root_pc)
        bar_start = i * beats_per_chord

        for b in range(beats):
            t = bar_start + b
            if b == 0:
                pitch = root_pitch
            elif b == beats - 1:
                # chromatic approach into the next root, from prev's side
                ref = prev if prev is not None else root_pitch
                approach = next_root - 1 if ref >= next_root else next_root + 1
                pitch = max(low, min(high, approach))
            elif prev is not None:
                pool = chord_pitches if rng.random() < 0.6 else scale_notes
                pitch = min(pool, key=lambda p: (abs(p - prev), p)) if pool else root_pitch
                # avoid repeating the exact previous note when a neighbour exists
                if pitch == prev:
                    alts = sorted(pool, key=lambda p: abs(p - prev))
                    pitch = alts[1] if len(alts) > 1 else pitch
            else:
                pitch = rng.choice(chord_pitches or scale_notes)

            vel = velocity if b == 0 else velocity - 12
            notes.append(_note(pitch, t, 0.9, vel))
            prev = pitch

            if ghost and b < beats - 1 and rng.random() < ghost:
                notes.append(_note(pitch, t + 0.5, 0.25, velocity - 45))
    return notes


# ── 5. Drum groove (pocket + ghost notes + humanized velocity) ────────

# Instrument -> beat positions within one 4/4 bar.
GROOVE_STYLES = {
    "lofi": {"kick": [0.0, 2.5], "snare": [1.0, 3.0], "chat": [i * 0.5 for i in range(8)]},
    "boom_bap": {"kick": [0.0, 1.5, 2.0], "snare": [1.0, 3.0], "chat": [i * 0.5 for i in range(8)]},
    "house": {"kick": [0.0, 1.0, 2.0, 3.0], "clap": [1.0, 3.0], "ohat": [0.5, 1.5, 2.5, 3.5]},
    "trap": {"kick": [0.0, 2.5], "snare": [2.0], "chat": [i * 0.25 for i in range(16)]},
    "jazz": {"kick": [0.0], "snare": [1.0, 3.0], "ride": [0.0, 1.0, 1.5, 2.0, 3.0, 3.5]},
    "funk": {"kick": [0.0, 0.75, 2.5], "snare": [1.0, 3.0], "chat": [i * 0.25 for i in range(16)]},
}

_GHOST_SLOTS = [0.5, 1.5, 2.5, 3.25, 3.75]


def drum_groove(
    bars=2,
    style="lofi",
    pocket=0.02,
    swing=0.55,
    ghost=0.3,
    humanize=8,
    seed=None,
):
    """Generate a drum groove with a laid-back pocket, ghost snares, and humanized
    velocities.

    ``pocket`` shifts every hit later by that many beats (behind the beat).
    ``swing`` (0.5 straight .. ~0.66) delays off-beat hats. ``ghost`` is the
    per-slot probability of a soft ghost snare between the backbeats.
    ``humanize`` is the max +/- random velocity deviation. style keys:
    lofi/boom_bap/house/trap/jazz/funk. Uses the standard Live drum map. Returns
    note dicts.
    """
    if style not in GROOVE_STYLES:
        raise ValueError(f"unknown style '{style}'. Known: {sorted(GROOVE_STYLES)}")
    humanize = max(0, humanize)
    rng = random.Random(seed)
    plan = GROOVE_STYLES[style]
    base_vel = {"kick": 104, "snare": 96, "clap": 96, "chat": 52, "ohat": 70, "ride": 60}
    notes = []
    for bar in range(max(1, int(bars))):
        b0 = bar * 4.0
        for inst, positions in plan.items():
            pitch = DRUM_MAP[inst]
            for pos in positions:
                t = b0 + pos + pocket
                if pos % 1.0 != 0.0:  # swing the off-beats
                    t += (swing - 0.5) * 0.5
                vel = base_vel.get(inst, 80)
                if inst in ("chat", "ohat", "ride") and pos % 1.0 < 1e-6:
                    vel += 10  # accent the downbeats of the hat pattern
                vel += rng.randint(-humanize, humanize) if humanize else 0
                notes.append(_note(pitch, t, 0.22, vel))
        # ghost snares scattered between the backbeats
        if ghost:
            for slot in _GHOST_SLOTS:
                if rng.random() < ghost:
                    notes.append(
                        _note(DRUM_MAP["snare"], b0 + slot + pocket, 0.15, base_vel["snare"] * 0.35)
                    )
    return notes


# ── 6. Humanize ──────────────────────────────────────────────────────


def humanize(notes, timing=0.02, velocity=8, seed=None):
    """Return a new note list with random timing and velocity jitter applied.

    ``timing`` is the max +/- start-time deviation in beats; ``velocity`` the max
    +/- velocity deviation. Pitches are preserved, times kept non-negative, and
    velocities clamped to 1-127. Deterministic for a fixed ``seed``.
    """
    timing = max(0.0, timing)
    velocity = max(0, velocity)
    rng = random.Random(seed)
    out = []
    for n in notes:
        t = float(n["start_time"]) + (rng.uniform(-timing, timing) if timing else 0.0)
        v = int(n["velocity"]) + (rng.randint(-velocity, velocity) if velocity else 0)
        out.append(_note(n["pitch"], t, n["duration"], v, n.get("mute", False)))
    return out


# ── 7. Genre progressions ────────────────────────────────────────────


def _resolve_genre(genre):
    key = genre.strip().lower().replace(" ", "_").replace("-", "_")
    if key in GENRE_PROGRESSIONS:
        return GENRE_PROGRESSIONS[key]
    for k in GENRE_PROGRESSIONS:
        if key in k or k in key:
            return GENRE_PROGRESSIONS[k]
    raise ValueError(f"unknown genre '{genre}'. Known: {sorted(GENRE_PROGRESSIONS)}")


def progression_for_genre(genre, key="C", scale="minor", bars=4):
    """Build a genre-idiomatic chord progression, transposed into ``key``/``scale``.

    Looks up the genre's (scale_degree, quality) grammar and maps each degree to a
    real chord in the key. Loops or truncates to ``bars`` chords. Returns a list
    of chord symbols (e.g. ['Am7', 'Dm7', 'G7', 'Cmaj7']).
    """
    pattern = _resolve_genre(genre)
    root = _parse_note_name(key)
    intervals = _scale_intervals(scale)
    chords = []
    for b in range(max(1, int(bars))):
        degree, quality = pattern[b % len(pattern)]
        pc = (root + intervals[degree % len(intervals)]) % 12
        chords.append(PITCH_NAMES[pc] + quality)
    return chords


# ── wire write path (same commands generators.py uses) ───────────────


# ── tools ────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_euclidean_drums(
    ctx: Context,
    track_index: int,
    clip_index: int,
    instrument: str = "kick",
    pulses: int = 3,
    steps: int = 8,
    bars: int = 2,
    rotate: int = 0,
    velocity: int = 100,
    humanize: int = 6,
    seed: int | None = None,
) -> str:
    """Write a Euclidean (Bjorklund) rhythm for one drum voice into a Session clip.
    REPLACES the clip's notes if the clip exists.

    Distributes `pulses` hits as evenly as possible across `steps` slots spanning
    one bar (steps=8 -> 8th notes, 16 -> 16th notes), repeated for `bars`.
    pulses=3, steps=8 is the tresillo. `rotate` shifts the pattern left by that
    many steps. instrument: a drum-map name (kick/snare/chat/ohat/clap/ride/...) or
    a raw MIDI number. `humanize` is max +/- velocity jitter.
    """
    pattern = euclidean(pulses, steps, rotate)
    pitch = DRUM_MAP.get(instrument.strip().lower())
    if pitch is None:
        if not instrument.strip().isdigit():
            raise ValueError(
                f"unknown instrument '{instrument}'. Known: {sorted(DRUM_MAP)} or a MIDI number"
            )
        pitch = int(instrument.strip())
    humanize = max(0, humanize)
    rng = random.Random(seed)
    step_beats = 4.0 / steps
    notes = []
    for bar in range(max(1, bars)):
        for i, hit in enumerate(pattern):
            if hit:
                v = velocity + (rng.randint(-humanize, humanize) if humanize else 0)
                notes.append(_note(pitch, bar * 4.0 + i * step_beats, 0.22, v))
    n = _write_clip(track_index, clip_index, max(1, bars) * 4.0, notes)
    return f"Wrote {n} euclidean hits E({pulses},{steps}) for '{instrument}' over {bars} bars to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_voiced_progression(
    ctx: Context,
    track_index: int,
    clip_index: int,
    progression: str,
    style: str = "rootless",
    beats_per_chord: float = 4.0,
    center_pitch: int = 60,
    velocity: int = 64,
    voices: int = 4,
    humanize: int = 4,
    seed: int | None = None,
) -> str:
    """Write voice-led jazz chord voicings into a Session clip. REPLACES existing notes.

    progression: dash-/comma-separated symbols, e.g. "Am9-Dm7-G13-Cmaj7".
    style: rootless (3-7-9-13 stack, no root), quartal (stacked 4ths), shell
    (root-3-7), or block (close-position). Smooth voice-leading is applied between
    chords. center_pitch biases register (60 = C3 in Live). voices caps notes per
    chord; humanize is max +/- velocity jitter.
    """
    chords = _split_progression(progression)
    notes = voice_progression(
        chords,
        style=style,
        beats_per_chord=beats_per_chord,
        center=center_pitch,
        velocity=velocity,
        voices=voices,
        humanize=humanize,
        seed=seed,
    )
    n = _write_clip(track_index, clip_index, len(chords) * beats_per_chord, notes)
    return (
        f"Wrote {len(chords)} {style} voicings ({n} notes) to track {track_index} slot {clip_index}"
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_melody(
    ctx: Context,
    track_index: int,
    clip_index: int,
    progression: str,
    key: str = "C",
    scale: str = "minor",
    beats_per_chord: float = 4.0,
    density: str = "medium",
    low: int = 60,
    high: int = 84,
    phrase_arc: str = "arch",
    chromatic: float = 0.15,
    swing: float = 0.5,
    seed: int | None = None,
) -> str:
    """Write a voice-led melody over a chord progression. REPLACES existing notes.

    Notes come from key+scale, biased to chord tones on strong beats, chosen by
    nearest-pitch voice-leading (small steps), with optional chromatic approach
    notes. progression: dash-/comma-separated symbols. key: e.g. "C", "F#", "Bb".
    scale: major/minor/dorian/lydian/mixolydian/blues/bebop/minor_pentatonic/...
    density: low|medium|high (note rate). phrase_arc: rising|arch|ascend_descend|
    static (register contour). chromatic: 0-1 approach-note probability. low/high
    bound the register (MIDI). swing: 0.5 straight .. ~0.66.
    """
    chords = _split_progression(progression)
    notes = melody_line(
        chords,
        key=key,
        scale=scale,
        beats_per_chord=beats_per_chord,
        density=density,
        low=low,
        high=high,
        phrase_arc=phrase_arc,
        chromatic=chromatic,
        swing=swing,
        seed=seed,
    )
    n = _write_clip(track_index, clip_index, len(chords) * beats_per_chord, notes)
    return f"Wrote {n}-note {density} melody over {len(chords)} chords to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_walking_bass(
    ctx: Context,
    track_index: int,
    clip_index: int,
    progression: str,
    key: str = "C",
    scale: str = "minor",
    beats_per_chord: float = 4.0,
    octave: int = 2,
    ghost: float = 0.15,
    velocity: int = 90,
    seed: int | None = None,
) -> str:
    """Write a quarter-note walking bass line under a progression. REPLACES notes.

    Root on beat 1, chord/scale tones through the bar, chromatic approach into the
    next chord's root on the last beat, with occasional soft ghost notes.
    progression: dash-/comma-separated symbols. key/scale set the passing-tone
    pool. octave: 1 = very low (C1), 2 = typical bass (C2). ghost: 0-1 offbeat
    ghost-note probability.
    """
    chords = _split_progression(progression)
    low = 12 * (octave + 1)
    notes = walking_bass(
        chords,
        key=key,
        scale=scale,
        beats_per_chord=beats_per_chord,
        low=low,
        high=low + 19,
        ghost=ghost,
        velocity=velocity,
        seed=seed,
    )
    n = _write_clip(track_index, clip_index, len(chords) * beats_per_chord, notes)
    return f"Wrote walking bass ({n} notes) over {len(chords)} chords to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_groove(
    ctx: Context,
    track_index: int,
    clip_index: int,
    style: str = "lofi",
    bars: int = 2,
    pocket: float = 0.02,
    swing: float = 0.55,
    ghost: float = 0.3,
    humanize: int = 8,
    seed: int | None = None,
) -> str:
    """Write a pocketed drum groove into a Session clip. REPLACES existing notes.

    style: lofi | boom_bap | house | trap | jazz | funk. pocket: beats to lay
    every hit behind the beat (0.0-0.1). swing: off-beat hat delay (0.5 straight
    .. ~0.66). ghost: 0-1 probability of soft ghost snares between backbeats.
    humanize: max +/- velocity deviation. Uses the standard Live drum map.
    """
    notes = drum_groove(
        bars=bars,
        style=style,
        pocket=pocket,
        swing=swing,
        ghost=ghost,
        humanize=humanize,
        seed=seed,
    )
    n = _write_clip(track_index, clip_index, max(1, bars) * 4.0, notes)
    return f"Wrote {n}-note {style} groove ({bars} bars) to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_genre_progression(
    ctx: Context,
    track_index: int,
    clip_index: int,
    genre: str,
    key: str = "C",
    scale: str = "minor",
    bars: int = 4,
    style: str = "rootless",
    beats_per_chord: float = 4.0,
    center_pitch: int = 60,
    velocity: int = 64,
    voices: int = 4,
    seed: int | None = None,
) -> str:
    """Build a genre-idiomatic chord progression and write it as voiced chords.
    REPLACES existing notes.

    genre: lofi/boom_bap/hip_hop/trap/house/deep_house/techno/trance/dnb/rnb/
    neo_soul/jazz/bossa_nova/ambient/synthwave/pop/afrobeats/amapiano/... (fuzzy
    matched). The genre's scale-degree grammar is transposed into key+scale, then
    voiced (see generate_voiced_progression for the style options). bars = number
    of chords (the grammar loops). Returns the chord symbols it used.
    """
    chords = progression_for_genre(genre, key=key, scale=scale, bars=bars)
    notes = voice_progression(
        chords,
        style=style,
        beats_per_chord=beats_per_chord,
        center=center_pitch,
        velocity=velocity,
        voices=voices,
        seed=seed,
    )
    _write_clip(track_index, clip_index, len(chords) * beats_per_chord, notes)
    return f"Wrote {genre} progression [{' '.join(chords)}] ({len(notes)} notes) to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def humanize_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    timing: float = 0.02,
    velocity: int = 8,
    seed: int | None = None,
) -> str:
    """Read a MIDI clip, apply timing + velocity humanization, and write it back.
    REPLACES the clip's notes with the jittered version.

    timing: max +/- start-time deviation in beats. velocity: max +/- velocity
    deviation. Pitches are preserved; times stay non-negative. seed makes it
    reproducible.
    """
    conn = get_ableton_connection()
    raw = conn.send_command(
        "get_clip_notes", {"track_index": track_index, "clip_index": clip_index}
    )
    src = [
        {
            "pitch": n["pitch"],
            "start_time": n["start_time"],
            "duration": n["duration"],
            "velocity": n["velocity"],
            "mute": n.get("mute", False),
        }
        for n in raw.get("notes", [])
    ]
    if not src:
        return f"No notes to humanize in track {track_index} slot {clip_index}"
    jittered = humanize(src, timing=timing, velocity=velocity, seed=seed)
    conn.send_command(
        "add_notes_to_clip",
        {"track_index": track_index, "clip_index": clip_index, "notes": jittered},
    )
    return f"Humanized {len(jittered)} notes (timing +/-{timing}, velocity +/-{velocity}) in track {track_index} slot {clip_index}"
