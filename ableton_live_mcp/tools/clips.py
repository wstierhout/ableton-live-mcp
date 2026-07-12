"""Clip creation, MIDI notes, quantize, grooves, and clip properties."""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import params

# Quantize grids, mirroring the Remote Script's _Q_BASE keys (sync-tested in
# test_dispatch.py). The client pre-check saves a round trip on typos.
VALID_GRIDS = (
    "quarter",
    "eighth",
    "eighth_triplet",
    "sixteenth",
    "sixteenth_triplet",
    "thirtysecond",
)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_clip(ctx: Context, track_index: int, clip_index: int, length: float = 4.0) -> str:
    """Create an empty MIDI clip in a Session slot. Fails if the slot already has
    a clip (pick another clip_index or delete_clip first) or the track is an
    audio track. length is in beats (4.0 = one 4/4 bar).
    Typical chain: create_clip -> add_notes_to_clip -> fire_clip or
    duplicate_to_arrangement.
    """
    ableton = get_ableton_connection()
    ableton.send_command(
        "create_clip", {"track_index": track_index, "clip_index": clip_index, "length": length}
    )
    return f"Created new clip at track {track_index}, slot {clip_index} with length {length} beats"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_audio_clip(ctx: Context, track_index: int, clip_index: int, path: str) -> str:
    """
    Create a new audio clip in an audio track's clip slot by importing a file.

    Requires Ableton Live 12.0.5 or newer - the underlying
    ClipSlot.create_audio_clip Live API was introduced in 12.0.5 and is not
    available in earlier 12.0.x releases.

    Parameters:
    - track_index: The index of the audio track to create the clip in
    - clip_index: The index of the clip slot to create the clip in
    - path: Absolute path to a supported audio file (e.g. a .wav). The target
      track must be an audio track and the clip slot must be empty.
    """
    ableton = get_ableton_connection()
    result = ableton.send_command(
        "create_audio_clip",
        {"track_index": track_index, "clip_index": clip_index, "path": path},
    )
    return f"Created audio clip '{result.get('name', 'clip')}' at track {track_index}, slot {clip_index} (length {result.get('length', '?')} beats)"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def add_notes_to_clip(
    ctx: Context, track_index: int, clip_index: int, notes: list[dict[str, int | float | bool]]
) -> str:
    """REPLACES the clip's entire note content with the given notes. Use edit_notes
    to add or remove a subset without touching the rest.

    notes: list of {"pitch": 0-127 (60 = C3 in Live's naming), "start_time": beats
    from clip start (float), "duration": beats, "velocity": 1-127, "mute": bool}.
    Precondition: the clip must exist (create_clip first).
    """
    ableton = get_ableton_connection()
    ableton.send_command(
        "add_notes_to_clip",
        {"track_index": track_index, "clip_index": clip_index, "notes": notes},
    )
    return f"Added {len(notes)} notes to clip at track {track_index}, slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_name(ctx: Context, track_index: int, clip_index: int, name: str) -> str:
    """
    Set the name of a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - name: The new name for the clip
    """
    ableton = get_ableton_connection()
    ableton.send_command(
        "set_clip_name", {"track_index": track_index, "clip_index": clip_index, "name": name}
    )
    return f"Renamed clip at track {track_index}, slot {clip_index} to '{name}'"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def fire_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Start playing a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    ableton = get_ableton_connection()
    ableton.send_command("fire_clip", {"track_index": track_index, "clip_index": clip_index})
    return f"Started playing clip at track {track_index}, slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def stop_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Stop playing a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    ableton = get_ableton_connection()
    ableton.send_command("stop_clip", {"track_index": track_index, "clip_index": clip_index})
    return f"Stopped clip at track {track_index}, slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """Delete a Session-view clip from a clip slot."""
    get_ableton_connection().send_command(
        "delete_clip", {"track_index": track_index, "clip_index": clip_index}
    )
    return f"Deleted clip at track {track_index}, slot {clip_index}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_clip_notes(ctx: Context, track_index: int, clip_index: int) -> str:
    """Read back all MIDI notes in a Session clip."""
    result = get_ableton_connection().send_command(
        "get_clip_notes", {"track_index": track_index, "clip_index": clip_index}
    )
    return json.dumps(result, indent=2)


# ── Grooves, quantize, audio clips, returns, automation, and clip ops ──


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_grooves(ctx: Context) -> str:
    """List grooves in the Groove Pool (index + name) and the global groove amount."""
    return json.dumps(get_ableton_connection().send_command("get_grooves"), indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_groove(
    ctx: Context, track_index: int, clip_index: int, groove_index: int | None = None
) -> str:
    """Attach a Groove Pool groove to a clip by index (swing/timing feel). Pass no groove_index to clear."""
    p = {"track_index": track_index, "clip_index": clip_index}
    if groove_index is not None:
        p["groove_index"] = groove_index
    r = get_ableton_connection().send_command("set_clip_groove", p)
    return f"Clip groove: {r.get('groove')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def quantize_clip(
    ctx: Context, track_index: int, clip_index: int, grid: str = "sixteenth", amount: float = 1.0
) -> str:
    """Quantize a MIDI clip. grid: quarter/eighth/eighth_triplet/sixteenth/sixteenth_triplet/thirtysecond.
    amount 0.0-1.0 (use <1.0 to humanize toward the grid without full snap)."""
    if grid not in VALID_GRIDS:
        raise ValueError(f"Unknown grid '{grid}'. Valid: {', '.join(VALID_GRIDS)}")
    get_ableton_connection().send_command(
        "quantize_clip",
        {"track_index": track_index, "clip_index": clip_index, "grid": grid, "amount": amount},
    )
    return f"Quantized to {grid} at {amount}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_audio(
    ctx: Context,
    track_index: int,
    clip_index: int,
    gain: float | None = None,
    pitch_coarse: int | None = None,
    pitch_fine: float | None = None,
    warping: bool | None = None,
    warp_mode: int | None = None,
) -> str:
    """Set audio-clip properties. gain 0-1, pitch_coarse semitones (-48..48), pitch_fine cents,
    warping on/off, warp_mode 0=Beats 1=Tones 2=Texture 3=Re-Pitch 4=Complex 6=Complex Pro."""
    opts = params(
        gain=gain,
        pitch_coarse=pitch_coarse,
        pitch_fine=pitch_fine,
        warping=warping,
        warp_mode=warp_mode,
    )
    if not opts:
        raise ValueError("Provide at least one property to set")
    r = get_ableton_connection().send_command(
        "set_clip_audio", {"track_index": track_index, "clip_index": clip_index, **opts}
    )
    return f"Applied: {json.dumps(r)}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_loop(
    ctx: Context,
    track_index: int,
    clip_index: int,
    start: float | None = None,
    end: float | None = None,
    start_marker: float | None = None,
    looping: bool | None = None,
) -> str:
    """Set a clip's loop braces (start/end beats), start marker, and looping on/off."""
    opts = params(start=start, end=end, start_marker=start_marker, looping=looping)
    if not opts:
        raise ValueError("Provide at least one property to set")
    r = get_ableton_connection().send_command(
        "set_clip_loop", {"track_index": track_index, "clip_index": clip_index, **opts}
    )
    return f"Applied: {json.dumps(r)}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def edit_notes(
    ctx: Context,
    track_index: int,
    clip_index: int,
    add: list[dict] | None = None,
    remove: list[dict] | None = None,
) -> str:
    """Edit a subset of notes in a MIDI clip without rewriting all of them.
    add = [{pitch,start_time,duration,velocity,mute}], remove = [{pitch,start_time}] (matched by pitch+start)."""
    r = get_ableton_connection().send_command(
        "edit_notes",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "add": add or [],
            "remove": remove or [],
        },
    )
    return f"Clip now has {r.get('note_count')} notes (added {r.get('added')})"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_color(ctx: Context, track_index: int, clip_index: int, color_index: int) -> str:
    """Set a clip's color by index (0-69 in Live's palette)."""
    get_ableton_connection().send_command(
        "set_clip_color",
        {"track_index": track_index, "clip_index": clip_index, "color_index": color_index},
    )
    return "Clip color set"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_clip_info(ctx: Context, track_index: int, clip_index: int) -> str:
    """Full clip state: loop points, markers, playing position, per-clip time
    signature; for audio clips also file path, gain display, and warp markers."""

    r = get_ableton_connection().send_command(
        "get_clip_info", {"track_index": track_index, "clip_index": clip_index}
    )
    return json.dumps(r, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def duplicate_clip_to(
    ctx: Context, src_track: int, src_scene: int, dst_track: int, dst_scene: int
) -> str:
    """Copy a Session clip to any other track/scene slot (overwrites the
    destination slot). The cross-track way to build variations."""
    get_ableton_connection().send_command(
        "duplicate_clip_to",
        {
            "src_track": src_track,
            "src_scene": src_scene,
            "dst_track": dst_track,
            "dst_scene": dst_scene,
        },
    )
    return f"Copied [{src_track},{src_scene}] -> [{dst_track},{dst_scene}]"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def clip_operation(
    ctx: Context,
    track_index: int,
    clip_index: int,
    op: str,
    region_start: float | None = None,
    region_length: float | None = None,
    destination_time: float | None = None,
    pitch: int | None = None,
    transposition_amount: int | None = None,
) -> str:
    """Structural clip edits. op: "duplicate_loop" (double the loop, duplicating
    notes - extend 2 bars to 4 before adding variation), "crop" (discard
    material outside the loop), "duplicate_region" (copy notes from
    region_start/region_length to destination_time, optionally transposing by
    transposition_amount semitones; pitch=-1 means all pitches)."""
    if op not in ("duplicate_loop", "crop", "duplicate_region"):
        raise ValueError(f"Unknown op '{op}'. Valid: duplicate_loop, crop, duplicate_region")
    op_params = {}
    if op == "duplicate_region":
        missing = [
            k
            for k, v in (
                ("region_start", region_start),
                ("region_length", region_length),
                ("destination_time", destination_time),
            )
            if v is None
        ]
        if missing:
            raise ValueError(f"duplicate_region requires: {', '.join(missing)}")
        op_params = params(
            region_start=region_start,
            region_length=region_length,
            destination_time=destination_time,
            pitch=pitch,
            transposition_amount=transposition_amount,
        )
    r = get_ableton_connection().send_command(
        "clip_op",
        {"track_index": track_index, "clip_index": clip_index, "op": op, "params": op_params},
    )
    return f"{op} done; clip length now {r.get('length')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_signature(
    ctx: Context, track_index: int, clip_index: int, numerator: int, denominator: int
) -> str:
    """Set a clip's own time signature (polymeter / odd-bar loops)."""
    r = get_ableton_connection().send_command(
        "set_clip_signature",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "numerator": numerator,
            "denominator": denominator,
        },
    )
    return f"Clip signature: {r.get('signature')}"
