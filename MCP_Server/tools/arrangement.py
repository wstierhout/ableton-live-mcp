"""Arrangement view, timeline placement, and clip automation."""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection


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


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def clear_automation(
    ctx: Context, track_index: int, clip_index: int, device_index: int, parameter: str | int
) -> str:
    """Clear the clip automation envelope for a device parameter."""
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
