"""Motif transformation + minimalist-process tools.

Two layers, like the rest of ``tools/``:

* **Pure functions** on lists of note dicts (``transpose`` / ``invert`` /
  ``retrograde`` / ``augment`` / ``phase_pattern`` / ``additive``). These do all
  the musical work in plain Python, take no Live connection, never mutate their
  inputs, and always return freshly-built, MIDI-clamped notes -- so they are
  deterministic and unit-testable with no running Live.
* Thin ``@mcp.tool`` wrappers that read notes from a Session clip over the
  ``get_clip_notes`` wire command, run a pure function, and write the result back
  through the shared ``_write_clip`` helper (``create_clip`` + ``add_notes_to_clip``).

The classical motif operators (transpose / inversion / retrograde / augmentation)
are the four canonical serial transformations. ``phase_pattern`` is Steve Reich's
phasing (a canon whose copies drift apart) and ``additive`` is Philip Glass's
additive process (a phrase that grows one note at a time).

Notes use the project's standard dict shape, identical to ``generators.py``:
``{"pitch": int 0-127, "start_time": float beats, "duration": float beats,
"velocity": int 1-127, "mute": bool}``. Pitches are clamped to 0-127, velocities
to 1-127, and start times kept non-negative.
"""

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from .generators import _note, _write_clip


def _mute_of(n):
    return bool(n.get("mute", False))


def _span(notes):
    """End time (in beats) of the last-ending note; 0.0 for an empty list."""
    return max((n["start_time"] + n["duration"] for n in notes), default=0.0)


# ── pure motif transformations ───────────────────────────────────────


def transpose(notes, semitones):
    """Shift every note's pitch by ``semitones`` (can be negative).

    Timing is untouched. Results outside 0-127 are clamped. Returns a new list;
    the input is not mutated.
    """
    s = int(semitones)
    return [
        _note(n["pitch"] + s, n["start_time"], n["duration"], n["velocity"], _mute_of(n))
        for n in notes
    ]


def invert(notes, axis_pitch=None):
    """Mirror every pitch about ``axis_pitch`` (melodic inversion).

    Each pitch ``p`` maps to ``2*axis - p`` (an up-third becomes a down-third,
    etc.). ``axis_pitch`` defaults to the first note's pitch, which leaves that
    note fixed. Timing is untouched; results are clamped to 0-127. Returns a new
    list; the input is not mutated.
    """
    if not notes:
        return []
    axis = notes[0]["pitch"] if axis_pitch is None else int(axis_pitch)
    return [
        _note(2 * axis - n["pitch"], n["start_time"], n["duration"], n["velocity"], _mute_of(n))
        for n in notes
    ]


def retrograde(notes, total=None):
    """Reverse the motif in time so the onset order flips (play it backwards).

    A note occupying ``[start, start+duration]`` is mapped to
    ``[total - (start+duration), total - start]`` -- i.e. each note is mirrored
    about ``total/2``, so the last-ending note now starts first while every
    duration is preserved. ``total`` is the length in beats to mirror around;
    when ``None`` it defaults to the motif's own end (the last note's end).
    Pitches and velocities are untouched. Returns a new list; the input is not
    mutated.
    """
    if not notes:
        return []
    t = _span(notes) if total is None else float(total)
    return [
        _note(
            n["pitch"],
            t - (n["start_time"] + n["duration"]),
            n["duration"],
            n["velocity"],
            _mute_of(n),
        )
        for n in notes
    ]


def augment(notes, factor):
    """Scale each note's ``start_time`` and ``duration`` by ``factor``.

    ``factor > 1`` stretches the motif (augmentation -- slower, longer);
    ``factor < 1`` compresses it (diminution -- faster, shorter). Pitches and
    velocities are untouched. Returns a new list; the input is not mutated.
    """
    f = float(factor)
    return [
        _note(n["pitch"], n["start_time"] * f, n["duration"] * f, n["velocity"], _mute_of(n))
        for n in notes
    ]


def phase_pattern(notes, repeats, shift):
    """Steve-Reich phasing: stack ``repeats`` copies of the motif, each copy
    ``shift`` beats later than the previous one.

    Copy ``k`` (0-indexed) is the whole motif with every start time offset by
    ``k * shift`` beats. With ``shift`` smaller than the motif's length the copies
    overlap and drift against one another (the shimmering phasing texture); with
    ``shift`` equal to the motif length it degenerates to a plain loop; larger
    leaves gaps. Pitches, durations and velocities are preserved. Returns a new
    list of ``repeats * len(notes)`` notes; the input is not mutated.
    """
    reps = max(0, int(repeats))
    out = []
    for k in range(reps):
        offset = k * float(shift)
        for n in notes:
            out.append(
                _note(
                    n["pitch"],
                    n["start_time"] + offset,
                    n["duration"],
                    n["velocity"],
                    _mute_of(n),
                )
            )
    return out


def additive(notes, steps):
    """Philip-Glass additive process: play the first 1 note, then the first 2,
    then the first 3 ... concatenated end to end.

    Stage ``i`` (for ``i`` in 1..``steps``) is the sub-motif of the first ``i``
    notes, re-anchored to begin where the previous stage ended, so the phrase
    audibly grows one note per stage. ``steps`` is capped at ``len(notes)``.
    Pitches, durations and velocities are preserved. Returns a new list; the
    input is not mutated.
    """
    if not notes:
        return []
    # Clip notes arrive in whatever order Live returns them; "first i notes"
    # only makes musical sense in time order.
    notes = sorted(notes, key=lambda n: n["start_time"])
    m = min(len(notes), max(1, int(steps)))
    base = notes[0]["start_time"]
    out = []
    cursor = 0.0
    for i in range(1, m + 1):
        sub = notes[:i]
        for n in sub:
            out.append(
                _note(
                    n["pitch"],
                    cursor + (n["start_time"] - base),
                    n["duration"],
                    n["velocity"],
                    _mute_of(n),
                )
            )
        cursor += _span(sub) - base
    return out


# ── wire helpers ─────────────────────────────────────────────────────

_TRANSFORMS = ("transpose", "invert", "retrograde", "augment")


def _read_clip_notes(track_index, clip_index):
    """Read a Session clip's notes and normalize them to the standard shape.

    Returns ``(notes, length)`` where ``length`` is the source clip's length in
    beats (used to preserve length when overwriting in place)."""
    conn = get_ableton_connection()
    raw = conn.send_command(
        "get_clip_notes", {"track_index": track_index, "clip_index": clip_index}
    )
    notes = [
        _note(
            n["pitch"],
            n["start_time"],
            n["duration"],
            n["velocity"],
            n.get("mute", False),
        )
        for n in raw.get("notes", [])
    ]
    return notes, float(raw.get("length", 0.0) or 0.0)


def _clip_length(notes, floor=1.0):
    return max(floor, round(_span(notes), 6))


def _parse_pitches(pitch_str):
    """Parse a space-/comma-separated list of MIDI pitch numbers, e.g.
    '60 62 64 67'. Non-numeric tokens raise ValueError."""
    tokens = pitch_str.replace(",", " ").split()
    if not tokens:
        raise ValueError("no pitches given; pass e.g. '60 62 64 67 69'")
    try:
        return [int(t) for t in tokens]
    except ValueError as e:
        raise ValueError(f"pitches must be MIDI numbers, got '{pitch_str}'") from e


def _motif_from_pitches(pitches, note_length, velocity):
    """Build a back-to-back one-voice motif: pitch i starts at i*note_length."""
    return [_note(p, i * note_length, note_length, velocity) for i, p in enumerate(pitches)]


# ── tools ────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def transform_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    op: str,
    amount: float = 0.0,
    dest_clip_index: int | None = None,
) -> str:
    """Read a MIDI clip's notes, apply a classical motif transformation, and write
    the result. Overwrites the source clip unless ``dest_clip_index`` is given
    (in which case that destination slot is REPLACED instead).

    op (one of):
      transpose  - shift every pitch by ``amount`` semitones (integer; may be
                   negative). Timing unchanged; out-of-range pitches clamped.
      invert     - mirror pitches about the first note's pitch (up-motion becomes
                   down-motion). ``amount`` is ignored.
      retrograde - play the motif backwards in time (onset order reversed,
                   durations preserved). ``amount`` is ignored.
      augment    - scale start times and durations by ``amount`` (a factor > 1
                   stretches/slows, 0 < ``amount`` < 1 compresses/speeds up).
                   Requires ``amount`` > 0.

    ``amount`` therefore means semitones for transpose and a time factor for
    augment. Returns a short summary of what was written.
    """
    op_key = op.strip().lower()
    if op_key not in _TRANSFORMS:
        raise ValueError(f"unknown op '{op}'. Known: {list(_TRANSFORMS)}")

    src, src_len = _read_clip_notes(track_index, clip_index)
    if not src:
        return f"No notes to transform in track {track_index} slot {clip_index}"

    if op_key == "transpose":
        result = transpose(src, amount)
        detail = f"transposed {amount:+.0f} semitones"
    elif op_key == "invert":
        result = invert(src)
        detail = f"inverted about pitch {src[0]['pitch']}"
    elif op_key == "retrograde":
        result = retrograde(src)
        detail = "retrograded"
    else:  # augment
        if amount <= 0:
            raise ValueError("augment needs amount > 0 (a time factor)")
        result = augment(src, amount)
        detail = f"augmented x{amount:g}"

    dest = clip_index if dest_clip_index is None else int(dest_clip_index)
    # Preserve length on an in-place transpose/invert/retrograde; recompute when
    # augmenting (it changes duration) or writing to a fresh destination slot.
    if dest == clip_index and op_key in ("transpose", "invert", "retrograde") and src_len > 0:
        length = src_len
    else:
        length = _clip_length(result)
    _write_clip(track_index, dest, length, result)
    return (
        f"{detail}: wrote {len(result)} notes from track {track_index} slot {clip_index} "
        f"to slot {dest}"
    )


def _motif_source(track_index, pitches, note_length, velocity, source_clip_index):
    """Read a motif from a source clip if given, else build one from a pitch list.
    Returns the note list (empty if the source clip has no notes)."""
    if source_clip_index is not None:
        motif, _ = _read_clip_notes(track_index, int(source_clip_index))
        return motif
    return _motif_from_pitches(_parse_pitches(pitches), note_length, velocity)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_phase(
    ctx: Context,
    track_index: int,
    clip_index: int,
    pitches: str = "60 62 64 67 69",
    note_length: float = 0.5,
    repeats: int = 8,
    shift: float = 0.25,
    velocity: int = 90,
    source_clip_index: int | None = None,
) -> str:
    """Write a Steve-Reich phasing pattern into a Session clip. REPLACES the
    clip's notes if it already exists.

    Builds a short one-voice motif (each pitch a ``note_length``-beat note, back
    to back) from ``pitches`` -- a space-/comma-separated list of MIDI numbers,
    e.g. "60 62 64 67 69". Then stacks ``repeats`` copies of that motif, each copy
    starting ``shift`` beats later than the last, so the copies drift against one
    another. A ``shift`` smaller than the motif length (len(pitches)*note_length)
    gives the classic overlapping phase texture; ``shift`` == motif length is a
    plain loop.

    If ``source_clip_index`` is given, the motif is read from that clip on the
    same track instead of built from ``pitches``. Returns a short summary.
    """
    motif = _motif_source(track_index, pitches, note_length, velocity, source_clip_index)
    if not motif:
        return f"No motif notes to phase (source clip empty?) on track {track_index}"

    notes = phase_pattern(motif, repeats, shift)
    length = _clip_length(notes)
    n = _write_clip(track_index, clip_index, length, notes)
    return (
        f"Wrote a {repeats}x phase pattern (shift {shift} beats, {n} notes) "
        f"to track {track_index} slot {clip_index}"
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_additive(
    ctx: Context,
    track_index: int,
    clip_index: int,
    pitches: str = "60 62 64 65 67 69",
    note_length: float = 0.5,
    steps: int = 0,
    velocity: int = 90,
    source_clip_index: int | None = None,
) -> str:
    """Write a Philip-Glass additive process into a Session clip. REPLACES the
    clip's notes if it already exists.

    Builds a short one-voice motif (each pitch a ``note_length``-beat note, back
    to back) from ``pitches`` -- a space-/comma-separated list of MIDI numbers.
    Then plays the first 1 note, then the first 2, then the first 3 ...
    concatenated end to end, so the phrase audibly grows one note per stage.
    ``steps`` is how many stages to build (0 = use every note; capped at the
    number of pitches).

    If ``source_clip_index`` is given, the motif is read from that clip on the
    same track instead of built from ``pitches``. Returns a short summary.
    """
    motif = _motif_source(track_index, pitches, note_length, velocity, source_clip_index)
    if not motif:
        return (
            f"No motif notes for the additive process (source clip empty?) on track {track_index}"
        )

    n_steps = len(motif) if steps <= 0 else steps
    notes = additive(motif, n_steps)
    length = _clip_length(notes)
    n = _write_clip(track_index, clip_index, length, notes)
    return (
        f"Wrote an additive build-up ({min(len(motif), n_steps)} stages, {n} notes) "
        f"to track {track_index} slot {clip_index}"
    )
