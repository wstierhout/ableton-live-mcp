"""Clip creation, MIDI notes, quantize, grooves, and clip properties."""

from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import (
    ClipIndex,
    ColorIndex,
    DisplayName,
    OptionalClipBeat,
    OptionalToggleState,
    TrackIndex,
    params,
)

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
def create_clip(track_index: int, clip_index: int, length: float = 4.0) -> dict:
    """Create an empty MIDI clip in a Session slot. Fails if the slot already has
    a clip (pick another clip_index or delete_clip first) or the track is an
    audio track. length is in beats (4.0 = one 4/4 bar).
    Typical chain: create_clip -> add_notes_to_clip -> fire_clip or
    duplicate_to_arrangement.
    """
    ableton = get_ableton_connection()
    return ableton.send_command(
        "create_clip", {"track_index": track_index, "clip_index": clip_index, "length": length}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_audio_clip(track_index: int, clip_index: int, path: str) -> dict:
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
    return ableton.send_command(
        "create_audio_clip",
        {"track_index": track_index, "clip_index": clip_index, "path": path},
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def add_notes_to_clip(
    track_index: int, clip_index: int, notes: list[dict[str, int | float | bool]]
) -> dict:
    """REPLACES the clip's entire note content with the given notes. Use edit_notes
    to add or remove a subset without touching the rest.

    notes: list of {"pitch": 0-127 (60 = C3 in Live's naming), "start_time": beats
    from clip start (float), "duration": beats, "velocity": 1-127, "mute": bool}.
    Precondition: the clip must exist (create_clip first).
    """
    ableton = get_ableton_connection()
    return ableton.send_command(
        "add_notes_to_clip",
        {"track_index": track_index, "clip_index": clip_index, "notes": notes},
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_clip_name(track_index: TrackIndex, clip_index: ClipIndex, name: DisplayName) -> dict:
    """Rename an existing Session-view clip without changing its notes or audio.

    The slot must contain a clip. Use this for the clip label; use set_track_name
    for the track label. Repeating the same name has no additional effect.
    """
    ableton = get_ableton_connection()
    return ableton.send_command(
        "set_clip_name", {"track_index": track_index, "clip_index": clip_index, "name": name}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def fire_clip(track_index: TrackIndex, clip_index: ClipIndex) -> dict:
    """Launch one existing Session-view clip using Live's launch quantization.

    The slot must contain a clip. Launching it takes Session control of that
    track and replaces any other playing Session clip on the same track; firing
    an already-playing clip may retrigger it according to its launch settings.
    Use fire_scene to launch a whole row, or continue_playing for transport only.
    """
    ableton = get_ableton_connection()
    return ableton.send_command("fire_clip", {"track_index": track_index, "clip_index": clip_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def stop_clip(track_index: int, clip_index: int) -> dict:
    """
    Stop playing a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    ableton = get_ableton_connection()
    return ableton.send_command("stop_clip", {"track_index": track_index, "clip_index": clip_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_clip(track_index: TrackIndex, clip_index: ClipIndex) -> dict:
    """Delete the existing Session-view clip in a slot, including its content.

    This leaves the slot empty and does not shift other slot indices. It fails
    for an empty slot. Use stop_clip if the intent is only to stop playback;
    use undo immediately if the deletion was accidental.
    """
    return get_ableton_connection().send_command(
        "delete_clip", {"track_index": track_index, "clip_index": clip_index}
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_clip_notes(track_index: TrackIndex, clip_index: ClipIndex) -> dict:
    """Return every MIDI note in a Session clip, up to 32,000; larger clips fail.

    Each note includes pitch, start time, duration, velocity, and mute state;
    newer Live versions may add expression fields. This is read-only and fails
    for an empty slot or audio clip. Use get_clip_info for clip properties,
    or detect_session_key for bounded analysis of a larger set.
    """
    return get_ableton_connection().send_command(
        "get_clip_notes", {"track_index": track_index, "clip_index": clip_index}
    )


# ── Grooves, quantize, audio clips, returns, automation, and clip ops ──


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_grooves() -> dict:
    """List grooves in the Groove Pool (index + name) and the global groove amount."""
    return get_ableton_connection().send_command("get_grooves")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_groove(track_index: int, clip_index: int, groove_index: int | None = None) -> dict:
    """Attach a Groove Pool groove to a clip by index (swing/timing feel). Pass no groove_index to clear."""
    p = {"track_index": track_index, "clip_index": clip_index}
    if groove_index is not None:
        p["groove_index"] = groove_index
    return get_ableton_connection().send_command("set_clip_groove", p)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def quantize_clip(
    track_index: int, clip_index: int, grid: str = "sixteenth", amount: float = 1.0
) -> dict:
    """Quantize a MIDI clip. grid: quarter/eighth/eighth_triplet/sixteenth/sixteenth_triplet/thirtysecond.
    amount 0.0-1.0 (use <1.0 to humanize toward the grid without full snap)."""
    if grid not in VALID_GRIDS:
        raise ValueError(f"Unknown grid '{grid}'. Valid: {', '.join(VALID_GRIDS)}")
    return get_ableton_connection().send_command(
        "quantize_clip",
        {"track_index": track_index, "clip_index": clip_index, "grid": grid, "amount": amount},
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_audio(
    track_index: int,
    clip_index: int,
    gain: float | None = None,
    pitch_coarse: int | None = None,
    pitch_fine: float | None = None,
    warping: bool | None = None,
    warp_mode: int | None = None,
) -> dict:
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
    return get_ableton_connection().send_command(
        "set_clip_audio", {"track_index": track_index, "clip_index": clip_index, **opts}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_clip_loop(
    track_index: TrackIndex,
    clip_index: ClipIndex,
    start: OptionalClipBeat = None,
    end: OptionalClipBeat = None,
    start_marker: OptionalClipBeat = None,
    looping: OptionalToggleState = None,
) -> dict:
    """Update selected loop and launch markers on one existing Session clip.

    Provide at least one optional field; omitted fields stay unchanged. `start`
    and `end` are loop-brace positions in clip-relative beats and end must be
    after start. `start_marker` controls where non-looped launch begins;
    `looping` enables or disables repetition. Use set_loop for the Arrangement's
    global loop region, not a clip, and get_clip_info to inspect current values.
    """
    opts = params(start=start, end=end, start_marker=start_marker, looping=looping)
    if not opts:
        raise ValueError("Provide at least one property to set")
    return get_ableton_connection().send_command(
        "set_clip_loop", {"track_index": track_index, "clip_index": clip_index, **opts}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def edit_notes(
    track_index: int,
    clip_index: int,
    add: list[dict] | None = None,
    remove: list[dict] | None = None,
) -> dict:
    """Edit a subset of notes in a MIDI clip without rewriting all of them.
    add = [{pitch,start_time,duration,velocity,mute}], remove = [{pitch,start_time}] (matched by pitch+start)."""
    return get_ableton_connection().send_command(
        "edit_notes",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "add": add or [],
            "remove": remove or [],
        },
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_clip_color(track_index: TrackIndex, clip_index: ClipIndex, color_index: ColorIndex) -> dict:
    """Set an existing Session clip's palette color without changing its content.

    This is a visual organization change only. Use set_track_color to recolor
    the track header instead. Repeating the same color has no additional effect.
    """
    return get_ableton_connection().send_command(
        "set_clip_color",
        {"track_index": track_index, "clip_index": clip_index, "color_index": color_index},
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_clip_info(track_index: int, clip_index: int) -> dict:
    """Full clip state: loop points, markers, playing position, per-clip time
    signature; for audio clips also file path, gain display, and warp markers."""

    return get_ableton_connection().send_command(
        "get_clip_info", {"track_index": track_index, "clip_index": clip_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def duplicate_clip_to(src_track: int, src_scene: int, dst_track: int, dst_scene: int) -> dict:
    """Copy a Session clip to any other track/scene slot (overwrites the
    destination slot). The cross-track way to build variations."""
    return get_ableton_connection().send_command(
        "duplicate_clip_to",
        {
            "src_track": src_track,
            "src_scene": src_scene,
            "dst_track": dst_track,
            "dst_scene": dst_scene,
        },
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def clip_operation(
    track_index: int,
    clip_index: int,
    op: str,
    region_start: float | None = None,
    region_length: float | None = None,
    destination_time: float | None = None,
    pitch: int | None = None,
    transposition_amount: int | None = None,
) -> dict:
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
    return get_ableton_connection().send_command(
        "clip_op",
        {"track_index": track_index, "clip_index": clip_index, "op": op, "params": op_params},
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_signature(track_index: int, clip_index: int, numerator: int, denominator: int) -> dict:
    """Set a clip's own time signature (polymeter / odd-bar loops)."""
    return get_ableton_connection().send_command(
        "set_clip_signature",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "numerator": numerator,
            "denominator": denominator,
        },
    )
