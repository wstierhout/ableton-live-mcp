"""Arrangement view, timeline placement, and clip automation."""

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import ClipIndex, DeviceIndex, DeviceParameter, TrackIndex, params


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def switch_to_arrangement_view() -> dict:
    """Switch Ableton's main window to the Arrangement view.

    Parameters:
    """
    ableton = get_ableton_connection()
    return ableton.send_command("switch_to_arrangement_view")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_arrangement_time(time: float) -> dict:
    """
    Move the arrangement playhead to a specific position.

    Parameters:
    - time: Position in beats from the start of the arrangement (e.g. 8.0 = bar 3 in 4/4)
    """
    ableton = get_ableton_connection()
    ableton.require_capability("confirmed_transport")
    return ableton.send_command("set_current_song_time", {"time": time})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_arrangement_clips(track_index: int) -> dict:
    """
    List all clips placed in the Arrangement timeline for a track.

    Returns each clip's name, start_time, end_time, length, and type.

    Parameters:
    - track_index: The index of the track to inspect
    """
    ableton = get_ableton_connection()
    return ableton.send_command("get_arrangement_clips", {"track_index": track_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def duplicate_to_arrangement(track_index: int, clip_index: int, destination_time: float) -> str:
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
def delete_arrangement_clip(track_index: int, arrangement_clip_index: int) -> dict:
    """Delete a clip from the Arrangement timeline by its position in
    get_arrangement_clips' list. NOTE: indices of later clips on the same track
    shift down by one after each delete - re-read get_arrangement_clips between
    deletes.
    """
    return get_ableton_connection().send_command(
        "delete_arrangement_clip",
        {"track_index": track_index, "arrangement_clip_index": arrangement_clip_index},
    )


AutomationPoints = Annotated[
    list[dict[str, float]],
    Field(min_length=1, max_length=4096, description="Ordered native breakpoints: time/value and optional positive duration in beats."),
]
AutomationMode = Literal["points", "ramp"]
RampStepLength = Annotated[float, Field(gt=0, le=4)]


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def write_automation(
    track_index: TrackIndex,
    clip_index: ClipIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    points: AutomationPoints,
    mode: AutomationMode = "points",
    step_length: RampStepLength = 0.0625,
) -> dict:
    """Replace a Session clip's parameter envelope with bounded steps or a ramp.

    Times are beats from clip start; values use the native min/max from
    get_device_parameters. In points mode, omitted duration holds until the next
    point or clip end. Explicit duration must be positive and cannot overlap the
    next point or extend past the clip. Points must be ordered and unique.

    Ramp mode joins ordered points as a staircase, at step_length beats (default
    0.0625), up to 4096 generated steps. Equal-time points make a jump. Only the
    last ramp point may specify duration. The last segment reaches its final
    value before its end. A point at clip end is an endpoint, not a zero-length
    inserted step. All input is validated before existing automation is cleared.

    A ramp starting at zero sets the parameter's value, then waits on later Live
    ticks before creating the envelope. The edit is submitted once; uncertain
    readback returns a warning, never a blind rewrite. Check start_settled and
    verify through read_automation, sampling just after step boundaries. Batch
    calls sequence the same deferred operation before following commands.
    """
    conn = get_ableton_connection()
    conn.require_capability("automation_v2")
    return conn.send_command(
        "write_automation",
        {"track_index": track_index, "clip_index": clip_index,
         "device_index": device_index, "parameter": parameter, "points": points,
         "mode": mode, "step_length": step_length},
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def read_automation(
    track_index: TrackIndex,
    clip_index: ClipIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    times: Annotated[list[float], Field(min_length=1, max_length=1024)] | None = None,
    start: Annotated[float, Field(ge=0)] | None = None,
    end: Annotated[float, Field(ge=0)] | None = None,
    step: Annotated[float, Field(gt=0)] | None = None,
) -> dict:
    """Read a Session clip envelope without creating or changing it.

    Choose explicit times OR an inclusive start/end/step grid (defaults 0 to clip
    end, every beat). At most 1024 finite samples within the clip per call.
    Returns native values, optional display text, parameter min/max/current value
    and has_envelope=false for a missing envelope. Samples are Live's actual
    stepped envelope, not a reconstruction of the supplied control points.
    On Live 11 an exact step boundary can return the value just before that
    step; sample just after the boundary to check the new value.
    """
    conn = get_ableton_connection()
    conn.require_capability("automation_readback")
    return conn.send_command(
        "read_automation",
        params(track_index=track_index, clip_index=clip_index,
               device_index=device_index, parameter=parameter,
               times=times, start=start, end=end, step=step),
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
def clear_automation(
    track_index: TrackIndex,
    clip_index: ClipIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
) -> dict:
    """Delete one device parameter's entire automation envelope from a clip.

    Resolve the device and parameter with get_device_parameters first. This
    removes every automation point for that parameter, not a time range; manual
    parameter state remains. Use write_automation to replace the envelope with
    new points, or undo immediately to recover an accidental clear.
    """
    return get_ableton_connection().send_command(
        "clear_automation",
        {
            "track_index": track_index,
            "clip_index": clip_index,
            "device_index": device_index,
            "parameter": parameter,
        },
    )
