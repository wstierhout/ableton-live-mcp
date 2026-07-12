"""Key / scale detection via the Krumhansl-Kessler algorithm (no dependencies).

`detect_key` is a pure function: it turns a list of MIDI notes into a 12-bin
pitch-class histogram (weighting each note by duration, velocity, their product,
or a flat count), then Pearson-correlates that histogram against all 24
major/minor key profiles and returns the best fit with a confidence and margin.
It has zero dependencies and never touches Live, so it unit-tests cleanly.

The tools wrap it over three note sources: a Session clip, a whole track, the
whole session (all via the socket), and a saved ``.als`` file (offline). The
histogram is scale-invariant, so any of the four weightings gives the same
tonic on a clean scale; `duration` and `product` best reflect what a listener
actually hears when note lengths vary.
"""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._als_parse import _load

# Krumhansl-Kessler key profiles: the perceived tonal hierarchy of the 12
# scale degrees, indexed from the tonic (index 0). These are the canonical
# 1982 probe-tone ratings; every tool here correlates against them.
KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

# Pitch-class names, sharp spelling, indexed 0=C .. 11=B.
PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Accepted note weightings -> how much a note adds to its pitch-class bin.
_WEIGHTS = ("duration", "velocity", "product", "count")


def _pearson(x, y):
    """Pearson correlation of two equal-length sequences, pure Python.

    Returns 0.0 when either sequence has zero variance (a flat histogram or
    profile carries no directional information), avoiding a divide-by-zero.
    """
    n = len(x)
    if n == 0:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x)
    dy = sum((b - my) ** 2 for b in y)
    den = (dx * dy) ** 0.5
    return num / den if den else 0.0


def _note_weight(note, weight):
    """Weight one note contributes to its pitch-class bin under `weight`."""
    dur = float(note.get("duration", 0.0) or 0.0)
    vel = float(note.get("velocity", 0.0) or 0.0)
    if weight == "duration":
        return dur
    if weight == "velocity":
        return vel
    if weight == "product":
        return dur * vel
    return 1.0  # "count"


def _histogram(notes, weight):
    """12-bin pitch-class histogram, each note folded to its pitch class and
    accumulated by `weight`. Notes without a usable pitch are skipped."""
    hist = [0.0] * 12
    for note in notes:
        pitch = note.get("pitch")
        if pitch is None:
            continue
        try:
            pc = int(pitch) % 12
        except (TypeError, ValueError):
            continue
        hist[pc] += _note_weight(note, weight)
    return hist


def _no_result(weight, message):
    """Uniform result shape for the cases where no key can be estimated."""
    return {
        "key": None,
        "tonic": None,
        "mode": None,
        "confidence": 0.0,
        "margin": 0.0,
        "runner_up": None,
        "weight": weight,
        "note_count": 0,
        "message": message,
    }


def detect_key(notes, weight="duration"):
    """Estimate the musical key of `notes` with the Krumhansl-Kessler algorithm.

    `notes` is a list of dicts shaped like our MIDI notes ({"pitch", "duration",
    "velocity", ...}); only pitch and the chosen weight field are read. `weight`
    is one of "duration", "velocity", "product" (duration*velocity), or "count".

    Returns a dict: {"key": "F minor", "tonic": "F", "mode": "minor",
    "confidence": <top correlation, -1..1>, "margin": <top minus runner-up>,
    "runner_up": "Ab major", "weight", "note_count"}. Empty (or pitchless /
    zero-weight) input returns a clear no-key result instead of raising.
    """
    if weight not in _WEIGHTS:
        raise ValueError(f"weight must be one of {_WEIGHTS}, got {weight!r}")

    notes = list(notes or [])
    if not notes:
        return _no_result(weight, "No notes to analyze.")

    hist = _histogram(notes, weight)
    if sum(hist) <= 0:
        return _no_result(weight, "Notes carry no weighted pitch content to analyze.")

    # Correlate the histogram against every tonic in both modes. For key with
    # tonic t, pitch class pc is expected to weigh profile[(pc - t) % 12].
    ranked = []
    for mode, profile in (("major", KK_MAJOR), ("minor", KK_MINOR)):
        for tonic in range(12):
            expected = [profile[(pc - tonic) % 12] for pc in range(12)]
            ranked.append((_pearson(expected, hist), tonic, mode))
    ranked.sort(key=lambda r: r[0], reverse=True)

    top_corr, top_tonic, top_mode = ranked[0]
    run_corr, run_tonic, run_mode = ranked[1]
    return {
        "key": f"{PITCH_CLASSES[top_tonic]} {top_mode}",
        "tonic": PITCH_CLASSES[top_tonic],
        "mode": top_mode,
        "confidence": round(top_corr, 4),
        "margin": round(top_corr - run_corr, 4),
        "runner_up": f"{PITCH_CLASSES[run_tonic]} {run_mode}",
        "weight": weight,
        "note_count": len(notes),
    }


def _normalize_notes(raw_notes):
    """Coerce socket/offline note dicts into our note shape, tolerating either
    "start_time" (Live socket) or "start" (offline .als parser) for the onset
    and skipping anything without a pitch."""
    out = []
    for note in raw_notes or []:
        if not isinstance(note, dict):
            continue
        pitch = note.get("pitch")
        if pitch is None:
            continue
        if note.get("mute"):
            # Deactivated notes are inaudible; they must not skew the key.
            continue
        start = note.get("start_time", note.get("start", 0.0))
        out.append(
            {
                "pitch": int(pitch),
                "start_time": float(start or 0.0),
                "duration": float(note.get("duration", 0.0) or 0.0),
                "velocity": float(note.get("velocity", 0.0) or 0.0),
            }
        )
    return out


# ── tools ──


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def detect_clip_key(
    ctx: Context, track_index: int, clip_index: int, weight: str = "duration"
) -> str:
    """Detect the key/scale of one Session MIDI clip (Krumhansl-Kessler). Reads
    the clip's notes and returns the best-fit key with a confidence (-1..1),
    margin over the runner-up, and the runner-up key. `weight` is how notes are
    weighted into the pitch-class histogram: "duration" (default), "velocity",
    "product", or "count". Reads only; changes nothing."""
    raw = get_ableton_connection().send_command(
        "get_clip_notes", {"track_index": track_index, "clip_index": clip_index}
    )
    notes = _normalize_notes(raw.get("notes", []))
    result = detect_key(notes, weight=weight)
    return json.dumps(
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "clip_name": raw.get("clip_name"),
            **result,
        },
        indent=2,
    )


def _pool_track_notes(conn, track_index, info):
    """Pool the notes of every Session clip on a track (skipping empty/audio slots).
    `info` is the track's get_track_info result. Returns (notes, clips_used)."""
    notes = []
    clips_used = 0
    for slot in info.get("clip_slots", []):
        if not slot.get("has_clip"):
            continue
        try:
            raw = conn.send_command(
                "get_clip_notes", {"track_index": track_index, "clip_index": slot.get("index")}
            )
        except Exception:
            continue  # audio clip or unreadable slot: skip, keep pooling MIDI
        added = _normalize_notes(raw.get("notes", []))
        if added:
            clips_used += 1
            notes.extend(added)
    return notes, clips_used


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def detect_track_key(ctx: Context, track_index: int, weight: str = "duration") -> str:
    """Detect the key/scale of a whole track by pooling the notes of every
    Session clip it holds (Krumhansl-Kessler). Useful when a part is spread
    across several clips. `weight` selects the histogram weighting ("duration",
    "velocity", "product", "count"). Reads only; changes nothing."""
    conn = get_ableton_connection()
    info = conn.send_command("get_track_info", {"track_index": track_index})
    notes, clips_used = _pool_track_notes(conn, track_index, info)
    result = detect_key(notes, weight=weight)
    return json.dumps(
        {
            "track_index": track_index,
            "track_name": info.get("name"),
            "clips_analyzed": clips_used,
            **result,
        },
        indent=2,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def detect_session_key(ctx: Context, weight: str = "duration") -> str:
    """Detect the overall key/scale of the whole set by pooling the notes of
    every MIDI track's Session clips (Krumhansl-Kessler). Gives the harmonic
    center of the arrangement as a whole. `weight` selects the histogram
    weighting ("duration", "velocity", "product", "count"). Reads only."""
    conn = get_ableton_connection()
    # One snapshot gives track types, so we only fetch clip slots for MIDI tracks.
    snapshot = conn.send_command("get_session_snapshot")
    notes = []
    tracks_used = 0
    for track in snapshot.get("tracks", []):
        if track.get("type") != "midi":
            continue
        info = conn.send_command("get_track_info", {"track_index": track["index"]})
        pooled, _ = _pool_track_notes(conn, track["index"], info)
        if pooled:
            tracks_used += 1
            notes.extend(pooled)
    result = detect_key(notes, weight=weight)
    return json.dumps({"tracks_analyzed": tracks_used, **result}, indent=2)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_detect_key(
    ctx: Context, path: str, track_index: int | None = None, weight: str = "duration"
) -> str:
    """Detect the key/scale of a saved .als file WITHOUT Live running
    (Krumhansl-Kessler). Pools notes from every MIDI clip in the set, or only
    one track when `track_index` is given. `weight` selects the histogram
    weighting ("duration", "velocity", "product", "count"). `path` is a
    filesystem path to a .als file."""
    data, err = _load(path)
    if err:
        raise ValueError(err)

    if track_index is not None:
        match = next((t for t in data["tracks"] if t["index"] == track_index), None)
        if match is None:
            raise ValueError(
                f"No track at index {track_index}; the set has {len(data['tracks'])} tracks."
            )
        source_tracks = [match]
    else:
        source_tracks = data["tracks"]

    notes = []
    for track in source_tracks:
        for clip in track["clips"]:
            if clip.get("is_midi"):
                notes.extend(_normalize_notes(clip.get("notes", [])))
    result = detect_key(notes, weight=weight)
    return json.dumps(
        {
            "path": data["path"],
            "track_index": track_index,
            **result,
        },
        indent=2,
    )
