"""Arrangement view, timeline placement, and clip automation."""

import json
from typing import Annotated

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import ClipIndex, DeviceIndex, DeviceParameter, TrackIndex


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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def read_automation(
    ctx: Context,
    track_index: TrackIndex,
    clip_index: ClipIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    times: Annotated[
        list[float] | None,
        Field(
            max_length=1024,
            description="Explicit beat positions from the clip start to sample. Overrides start/end/step.",
        ),
    ] = None,
    start: Annotated[
        float | None, Field(ge=0, description="First beat of the sample grid; default 0.")
    ] = None,
    end: Annotated[
        float | None, Field(ge=0, description="Last beat of the sample grid; default the clip end.")
    ] = None,
    step: Annotated[
        float | None, Field(gt=0, description="Beats between grid samples; default 1.0.")
    ] = None,
) -> str:
    """Read one device parameter's clip automation on a Session clip by sampling the
    envelope at beat positions, to verify what write_automation produced.

    Pass explicit times, or a start/end/step grid (default: every beat from the clip
    start to the clip end; at most 1024 samples). Values are native parameter values
    with a display string where Live provides one. has_envelope is false when the
    clip has no automation for that parameter. Read-only.

    At the exact time a step or breakpoint starts, Live returns the value just
    before it, so a grid that lands on step boundaries reads one step behind (and
    beat 0 reads the envelope's initial value). To check a step's value, sample a
    little after its start, e.g. times=[0.01, 4.01, 8.01].
    """
    r = get_ableton_connection().send_command(
        "read_automation",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "device_index": device_index,
            "parameter": parameter,
            "times": times,
            "start": start,
            "end": end,
            "step": step,
        },
    )
    return json.dumps(r, indent=2)
