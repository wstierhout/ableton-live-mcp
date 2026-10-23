"""Arrangement view, timeline placement, clip automation, and track automation."""

import json
from typing import Any

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import (
    ArrangementBeat,
    ClipIndex,
    DeviceIndex,
    DeviceParameter,
    DeviceParameterValue,
    TrackIndex,
    params,
)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def switch_to_arrangement_view(ctx: Context) -> str:
    """Switch Ableton's main window to the Arrangement view.

    Parameters:
    """
    ableton = get_ableton_connection()
    ableton.send_command("switch_to_arrangement_view")
    return "Switched to Arrangement view"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_arrangement_time(ctx: Context, time: float) -> str:
    """
    Move the arrangement playhead to a specific position.

    Parameters:
    - time: Position in beats from the start of the arrangement (e.g. 8.0 = bar 3 in 4/4)
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("set_current_song_time", {"time": time})
    return f"Playhead moved to beat {result.get('current_song_time', time)}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_arrangement_clips(ctx: Context, track_index: int) -> str:
    """
    List all clips placed in the Arrangement timeline for a track.

    Returns each clip's name, start_time, end_time, length, and type.

    Parameters:
    - track_index: The index of the track to inspect
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("get_arrangement_clips", {"track_index": track_index})
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def duplicate_to_arrangement(
    ctx: Context, track_index: int, clip_index: int, destination_time: float
) -> str:
    """
    Copy a Session-view clip into the Arrangement timeline.

    OVERWRITES whatever already occupies the destination range on that track
    (like recording over tape) - this is also the supported way to REPLACE a
    section. Uses Live's track.duplicate_clip_to_arrangement() API (Live 11/12).
    The clip is placed at destination_time beats from the start of the
    arrangement on the same track it lives in.

    Typical workflow:
      1. create_clip / add_notes_to_clip to build a Session clip
      2. Call duplicate_to_arrangement once per bar/section you need
      3. Call switch_to_arrangement_view to confirm the result in Live

    Parameters:
    - track_index:       Index of the track that owns the Session clip
    - clip_index:        Index of the clip slot in that track (Session view)
    - destination_time:  Beat position in the arrangement to place the clip
                         (e.g. 0.0 = start, 8.0 = bar 3 in 4/4)
    """
    ableton = get_ableton_connection()
    result = ableton.send_command(
        "duplicate_session_clip_to_arrangement",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "destination_time": destination_time,
        },
    )
    clip_name = result.get("clip_name", "clip")
    track_name = result.get("track_name", f"track {track_index}")
    return (
        f"Duplicated '{clip_name}' from Session slot {clip_index} "
        f"on '{track_name}' to arrangement at beat {destination_time}"
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_arrangement_clip(ctx: Context, track_index: int, arrangement_clip_index: int) -> str:
    """Delete a clip from the Arrangement timeline by its position in
    get_arrangement_clips' list. NOTE: indices of later clips on the same track
    shift down by one after each delete - re-read get_arrangement_clips between
    deletes.
    """
    result = get_ableton_connection().send_command(
        "delete_arrangement_clip",
        {"track_index": track_index, "arrangement_clip_index": arrangement_clip_index},
    )
    return f"Deleted arrangement clip '{result.get('name')}' ({result.get('start_time')}-{result.get('end_time')})"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def write_automation(
    ctx: Context,
    track_index: int,
    clip_index: int,
    device_index: int,
    parameter: str | int,
    points: list[dict[str, float]],
) -> str:
    """Write clip automation for a device parameter. points = [{"time": beats, "value": v}, ...].
    Replaces any existing envelope for that parameter on the clip."""
    r = get_ableton_connection().send_command(
        "write_automation",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "device_index": device_index,
            "parameter": parameter,
            "points": points,
        },
    )
    return f"Wrote {r.get('point_count')} automation points for {r.get('parameter')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
def clear_automation(
    ctx: Context,
    track_index: TrackIndex,
    clip_index: ClipIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
) -> str:
    """Delete one device parameter's entire automation envelope from a clip.

    Resolve the device and parameter with get_device_parameters first. This
    removes every automation point for that parameter, not a time range; manual
    parameter state remains. Use write_automation to replace the envelope with
    new points, or undo immediately to recover an accidental clear.
    """
    r = get_ableton_connection().send_command(
        "clear_automation",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "device_index": device_index,
            "parameter": parameter,
        },
    )
    return f"Cleared automation for {r.get('parameter')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def write_arrangement_automation(
    ctx: Context,
    track_index: TrackIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    points: list[dict[str, Any]],
    extra_curves: list[dict[str, Any]] | None = None,
    resolution: float = 0.125,
) -> str:
    """Write real track automation for a device parameter across the Arrangement
    timeline (filter sweeps, builds, fades, ducking), not just inside one clip.

    points = [{"time": beats, "value": v, "step": false}, ...]. time is in ABSOLUTE
    Arrangement beats (bar N starts at (N-1)*4 in 4/4). value is a native number or
    display text such as "-6 dB" or "250 Hz", resolved like set_device_parameter.
    Values ramp linearly between points, written as steps `resolution` beats wide
    (default 1/32 note). "step": true holds a point's value until the next point;
    use it for jumps and on/off switches. Before the first and after the last point
    the end values are held.

    How it works: Live cannot write envelopes into Arrangement clips, so every MIDI
    clip on the track that overlaps the curve is REBUILT. It is copied through an
    empty Session slot with the same notes, name and color plus the curve, then
    duplicated back onto its own position, where Live turns the envelope into track
    automation over that clip's whole span. The lane outside rebuilt clips is left
    untouched. One undo step reverts the whole call.

    Requirements and limits: the track needs one empty Session slot and MIDI clips
    covering the range. Audio clips, and clips looped or trimmed inside the
    Arrangement, are skipped and listed under "skipped". Group tracks and tracks
    without clips cannot be automated this way. Rebuilding a clip can reset other
    automated parameters over its span, so pass every parameter that should change
    in the same span through extra_curves (items shaped
    {"device_index": int, "parameter": name or index, "points": [...]}) to write
    them together. Verify afterwards with get_device_parameters at a few playhead
    positions while the transport is stopped.
    """
    curves = [{"device_index": device_index, "parameter": parameter, "points": points}]
    curves += extra_curves or []
    result = get_ableton_connection().send_command(
        "write_arrangement_automation",
        {"track_index": track_index, "curves": curves, "resolution": resolution},
    )
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
def clear_arrangement_automation(
    ctx: Context,
    track_index: TrackIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    value: DeviceParameterValue,
    start_time: ArrangementBeat = 0.0,
    end_time: ArrangementBeat | None = None,
) -> str:
    """Flatten a device parameter's track automation to one constant value.

    Live's API cannot delete track automation, so every MIDI clip overlapping
    start_time..end_time (beats; end_time defaults to the end of the track's last
    clip) is rebuilt like write_arrangement_automation and its whole span is set to
    `value` (native number or display text such as "0 dB"). Same requirements:
    one empty Session slot on the track; audio and looped/trimmed clips are skipped.
    One undo step reverts the call.
    """
    result = get_ableton_connection().send_command(
        "clear_arrangement_automation",
        params(
            track_index=track_index,
            device_index=device_index,
            parameter=parameter,
            value=value,
            start_time=start_time,
            end_time=end_time,
        ),
    )
    return json.dumps(result, indent=2)
