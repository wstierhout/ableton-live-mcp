"""Server-side musical generators: patterns computed here, written via wire commands.

These tools do music-theory work locally and send plain note data to Live, so
they carry zero Live-API risk and always compose with the primitive tools.
"""

import json
import random

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection

# ── music theory tables ──────────────────────────────────────────────

NOTE_NAMES = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
}

CHORD_QUALITIES = {  # intervals from root
    "": [0, 4, 7],
    "m": [0, 3, 7],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "6": [0, 4, 7, 9],
    "m6": [0, 3, 7, 9],
    "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10],
    "m7b5": [0, 3, 6, 10],
    "dim7": [0, 3, 6, 9],
    "9": [0, 4, 7, 10, 14],
    "maj9": [0, 4, 7, 11, 14],
    "m9": [0, 3, 7, 10, 14],
    "11": [0, 4, 7, 10, 14, 17],
    "m11": [0, 3, 7, 10, 14, 17],
    "13": [0, 4, 7, 10, 14, 21],
    "maj13": [0, 4, 7, 11, 14, 21],
    "7#5": [0, 4, 8, 10],
    "7b5": [0, 4, 6, 10],
    "7#9": [0, 4, 7, 10, 15],
    "7b9": [0, 4, 7, 10, 13],
    "add9": [0, 4, 7, 14],
    "madd9": [0, 3, 7, 14],
}

# General MIDI-ish Live drum-rack map
DRUM_MAP = {
    "kick": 36,
    "rim": 37,
    "snare": 38,
    "clap": 39,
    "chat": 42,
    "phat": 44,
    "ohat": 46,
    "ltom": 41,
    "mtom": 45,
    "htom": 48,
    "crash": 49,
    "ride": 51,
    "perc": 47,
    "shaker": 70,
}

# style -> {instrument: [(step16, velocity), ...]} per bar (16 steps)
DRUM_STYLES = {
    "lofi": {
        "kick": [(0, 100), (7, 82), (10, 90)],
        "snare": [(4, 92), (12, 94)],
        "chat": [(0, 46), (2, 34), (4, 44), (6, 32), (8, 46), (10, 34), (12, 44), (14, 36)],
    },
    "boom_bap": {
        "kick": [(0, 105), (10, 92)],
        "snare": [(4, 100), (12, 102)],
        "chat": [(i, 70 if i % 4 == 0 else 45) for i in range(0, 16, 2)],
    },
    "house": {
        "kick": [(i, 105) for i in (0, 4, 8, 12)],
        "clap": [(4, 92), (12, 92)],
        "ohat": [(2, 70), (6, 70), (10, 70), (14, 70)],
        "chat": [(i, 40) for i in range(1, 16, 2)],
    },
    "techno": {
        "kick": [(i, 110) for i in (0, 4, 8, 12)],
        "chat": [(i, 55) for i in range(2, 16, 4)],
        "rim": [(7, 60), (15, 55)],
        "clap": [(4, 85), (12, 85)],
    },
    "trap": {
        "kick": [(0, 108), (6, 95), (10, 100)],
        "snare": [(8, 104)],
        "chat": [(i, 58 if i % 2 == 0 else 40) for i in range(16)],
    },
    "dnb": {
        "kick": [(0, 108), (10, 100)],
        "snare": [(4, 105), (12, 105)],
        "chat": [(i, 50) for i in range(0, 16, 2)],
        "ride": [(8, 60)],
    },
    "ambient": {"kick": [(0, 85)], "shaker": [(4, 40), (8, 44), (12, 40)]},
}


def _split_progression(progression):
    # Accept space-, comma-, or dash-separated symbols (chord symbols contain
    # none of those), e.g. "Am9-Dm7", "Am9, Dm7", or "Am9 Dm7".
    symbols = progression.replace(",", " ").replace("-", " ").split()
    if not symbols:
        raise ValueError("Empty progression")
    return symbols


def _parse_chord(symbol):
    """'Am9' -> (9, [0,3,7,10,14]); 'F#maj7' -> (6, ...)."""
    symbol = symbol.strip()
    root = symbol[0].upper()
    rest = symbol[1:]
    if rest[:1] in ("#", "b"):
        root += "#" if rest[0] == "#" else "B"
        rest = rest[1:]
    if root not in NOTE_NAMES:
        raise ValueError(f"Unknown chord root in '{symbol}'")
    quality = rest
    if quality not in CHORD_QUALITIES:
        raise ValueError(
            f"Unknown chord quality '{quality}' in '{symbol}'. Known: {sorted(CHORD_QUALITIES)}"
        )
    return NOTE_NAMES[root], CHORD_QUALITIES[quality]


def _voice(root_pc, intervals, center=60):
    """Voice a chord compactly around `center` (rootless-friendly)."""
    pitches = []
    for iv in intervals:
        p = root_pc + iv
        while p < center - 8:
            p += 12
        while p > center + 10:
            p -= 12
        while p < 0:
            p += 12
        while p > 127:
            p -= 12
        pitches.append(p)
    return sorted(set(pitches))


def _note(pitch, start, dur, vel):
    return {
        "pitch": int(pitch),
        "start_time": round(start, 4),
        "duration": round(dur, 4),
        "velocity": int(max(1, min(127, vel))),
        "mute": False,
    }


def _write_clip(track_index, clip_index, length, notes):
    """Create the clip if the slot is empty, then REPLACE its notes (2 wire calls)."""
    conn = get_ableton_connection()
    try:
        conn.send_command(
            "create_clip", {"track_index": track_index, "clip_index": clip_index, "length": length}
        )
    except Exception as e:
        if "already" not in str(e).lower() and "has a clip" not in str(e).lower():
            raise
    conn.send_command(
        "add_notes_to_clip", {"track_index": track_index, "clip_index": clip_index, "notes": notes}
    )
    return len(notes)


# ── tools ────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_drum_pattern(
    ctx: Context,
    track_index: int,
    clip_index: int,
    style: str = "lofi",
    bars: int = 2,
    swing: float = 0.06,
    humanize: int = 6,
    seed: int | None = None,
) -> str:
    """Write a genre drum pattern into a Session clip on a Drum Rack track.
    REPLACES the clip's notes if the clip exists.

    style: lofi | boom_bap | house | techno | trap | dnb | ambient.
    swing: beats to delay 8th-note offbeats (0.0-0.1; 0.06 ≈ MPC-ish).
    humanize: max random velocity deviation (0 = machine-perfect).
    Uses the standard Live drum map (36 kick, 38 snare, 42/46 hats...).
    """
    if style not in DRUM_STYLES:
        raise ValueError(f"Unknown style '{style}'. Known: {sorted(DRUM_STYLES)}")
    rng = random.Random(seed)
    notes = []
    for bar in range(bars):
        for inst, hits in DRUM_STYLES[style].items():
            for step, vel in hits:
                t = bar * 4 + step * 0.25
                if swing and (step % 4) == 2:
                    t += swing
                v = vel + (rng.randint(-humanize, humanize) if humanize else 0)
                notes.append(_note(DRUM_MAP[inst], t, 0.22, v))
    n = _write_clip(track_index, clip_index, bars * 4, notes)
    return f"Wrote {n} notes ({style}, {bars} bars) to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_chord_progression(
    ctx: Context,
    track_index: int,
    clip_index: int,
    progression: str,
    beats_per_chord: float = 4.0,
    center_pitch: int = 60,
    velocity: int = 62,
    strum: float = 0.02,
    rhythm: str = "held",
) -> str:
    """Write a chord progression into a Session clip. REPLACES existing notes.

    progression: dash- or comma-separated symbols, e.g. "Am9-Fmaj7-Cmaj9-G13"
    or "Dm7, G7, Cmaj7". Qualities: maj7/m7/m9/9/13/7#9/sus4/dim/add9 etc.
    rhythm: "held" (one chord per slot), "stabs" (hit on 1 and the and-of-3),
    "quarters" (four hits per bar, velocity-tapered).
    strum: seconds-ish stagger between chord tones (0 = block chord).
    """
    symbols = _split_progression(progression)
    notes = []
    for i, sym in enumerate(symbols):
        root_pc, intervals = _parse_chord(sym)
        pitches = _voice(root_pc, intervals, center_pitch)
        t0 = i * beats_per_chord
        if rhythm == "held":
            hits = [(t0, beats_per_chord * 0.95, velocity)]
        elif rhythm == "stabs":
            hits = [
                (t0, beats_per_chord * 0.55, velocity),
                (t0 + beats_per_chord * 0.625, beats_per_chord * 0.2, velocity - 14),
            ]
        elif rhythm == "quarters":
            hits = [(t0 + q, 0.9, velocity - (q % 2) * 12) for q in range(int(beats_per_chord))]
        else:
            raise ValueError("rhythm must be held | stabs | quarters")
        for start, dur, vel in hits:
            for j, p in enumerate(pitches):
                notes.append(_note(p, start + j * strum, dur, vel))
    n = _write_clip(track_index, clip_index, len(symbols) * beats_per_chord, notes)
    return f"Wrote {len(symbols)} chords ({n} notes) to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def generate_bassline(
    ctx: Context,
    track_index: int,
    clip_index: int,
    progression: str,
    style: str = "roots",
    octave: int = 2,
    beats_per_chord: float = 4.0,
    velocity: int = 88,
) -> str:
    """Write a bassline following a chord progression. REPLACES existing notes.

    progression: same syntax as generate_chord_progression.
    style: "roots" (long root notes), "root_fifth" (root + fifth bounce),
    "walking" (root, approach tones), "eighth_pump" (driving 8ths on the root).
    octave: 1 = very low (C1=24), 2 = typical bass (C2=36).
    """
    symbols = _split_progression(progression)
    base = 12 * (octave + 1)
    notes = []
    for i, sym in enumerate(symbols):
        root_pc, intervals = _parse_chord(sym)
        root = base + root_pc
        fifth = root + (intervals[2] if len(intervals) > 2 else 7)
        t0 = i * beats_per_chord
        nxt_pc, _ = _parse_chord(symbols[(i + 1) % len(symbols)])
        nxt = base + nxt_pc
        if style == "roots":
            notes.append(_note(root, t0, beats_per_chord * 0.9, velocity))
        elif style == "root_fifth":
            notes += [
                _note(root, t0, 1.4, velocity),
                _note(fifth, t0 + 2.0, 0.9, velocity - 10),
                _note(root, t0 + 3.0, 0.9, velocity - 6),
            ]
        elif style == "walking":
            approach = nxt + (1 if root < nxt else -1)
            notes += [
                _note(root, t0, 1.4, velocity),
                _note(root, t0 + 2.5, 0.45, velocity - 14),
                _note(fifth, t0 + 3.0, 0.45, velocity - 10),
                _note(approach, t0 + 3.5, 0.45, velocity - 8),
            ]
        elif style == "eighth_pump":
            notes += [
                _note(root, t0 + e * 0.5, 0.42, velocity - (e % 2) * 12)
                for e in range(int(beats_per_chord * 2))
            ]
        else:
            raise ValueError("style must be roots | root_fifth | walking | eighth_pump")
    n = _write_clip(track_index, clip_index, len(symbols) * beats_per_chord, notes)
    return f"Wrote {style} bassline ({n} notes) to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def write_drum_grid(
    ctx: Context,
    track_index: int,
    clip_index: int,
    grid: str,
    swing: float = 0.0,
) -> str:
    """Write drums from an ASCII grid (16th-note steps). REPLACES existing notes.

    grid: one line per instrument: "name: pattern" where pattern chars are
    'X' (accent, vel 105), 'x' (normal, 85), 'o' (soft, 55), '.' or '-' (rest).
    Line length = number of 16th steps (16 = one 4/4 bar; 32 = two bars...).
    Instruments: kick snare rim clap chat phat ohat ltom mtom htom crash ride perc shaker,
    or a raw MIDI number ("36: x...").

    Example (one bar of lofi):
      kick:  X..x......X.....
      snare: ....x.......x...
      chat:  x.o.x.o.x.o.x.o.
    """
    VEL = {"X": 105, "x": 85, "o": 55}
    notes = []
    max_steps = 0
    for line in grid.strip().splitlines():
        if ":" not in line:
            continue
        name, pattern = line.split(":", 1)
        name = name.strip().lower()
        pattern = pattern.strip().replace(" ", "")
        pitch = DRUM_MAP.get(name)
        if pitch is None:
            if not name.isdigit():
                raise ValueError(
                    f"Unknown instrument '{name}'. Known: {sorted(DRUM_MAP)} or a MIDI number"
                )
            pitch = int(name)
        max_steps = max(max_steps, len(pattern))
        for step, ch in enumerate(pattern):
            if ch in VEL:
                t = step * 0.25
                if swing and (step % 4) == 2:
                    t += swing
                notes.append(_note(pitch, t, 0.22, VEL[ch]))
    if not notes:
        raise ValueError("Grid contained no hits")
    length = max(4.0, (max_steps * 0.25))
    n = _write_clip(track_index, clip_index, length, notes)
    return f"Wrote {n} hits over {max_steps} steps to track {track_index} slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def setup_session(ctx: Context, tempo: float, tracks: list[dict]) -> str:
    """Bootstrap a session in one call: set tempo and create named MIDI tracks,
    optionally loading an instrument found by name for each.

    tracks: [{"name": "Drums", "instrument": "drum kit"}, {"name": "Bass",
    "instrument": "upright bass"}, ...]. "instrument" is a search_browser query
    (best match is loaded); omit it to create an empty track.
    Returns a per-track report with the loaded item names.
    """
    conn = get_ableton_connection()
    # one round-trip / one undo step for tempo + creation; a second for renames
    created = conn.send_command(
        "batch",
        {
            "commands": [{"type": "set_tempo", "params": {"tempo": tempo}}]
            + [{"type": "create_midi_track", "params": {"index": -1}} for _ in tracks]
        },
    )
    indices = [r["result"]["index"] for r in created["results"][1:]]
    conn.send_command(
        "batch",
        {
            "commands": [
                {
                    "type": "set_track_name",
                    "params": {"track_index": idx, "name": spec.get("name", f"Track {idx}")},
                }
                for idx, spec in zip(indices, tracks)
            ]
        },
    )
    report = []
    for idx, spec in zip(indices, tracks):
        entry = {"track_index": idx, "name": spec.get("name")}
        query = spec.get("instrument")
        if query:
            found = conn.send_command("search_browser", {"query": query, "max_results": 1})
            matches = found.get("matches", [])
            if matches:
                conn.send_command(
                    "load_browser_item", {"track_index": idx, "item_uri": matches[0]["uri"]}
                )
                entry["loaded"] = matches[0]["name"]
            else:
                entry["loaded"] = None
                entry["note"] = f"no browser match for '{query}'"
        report.append(entry)
    return json.dumps({"tempo": tempo, "tracks": report}, indent=2)


# Advanced generators (voice-leading, jazz voicings, euclidean, genre progressions)
# live in a sibling module; importing here registers them as part of this toolset.
from . import generators_advanced  # noqa: E402, F401
