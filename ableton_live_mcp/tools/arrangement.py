"""Arrangement view, timeline placement, and clip automation."""

import json
import time
from typing import Annotated, Literal

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


AutomationPoints = Annotated[
    list[dict[str, float]],
    Field(
        min_length=1,
        description=(
            'Breakpoints as {"time": beats from clip start, "value": native parameter '
            'value, "duration": optional beats to hold the value}.'
        ),
    ),
]
AutomationMode = Annotated[
    Literal["points", "ramp"],
    Field(
        description=(
            '"points" writes each point as a step of its duration (default 0, a spike); '
            '"ramp" joins consecutive points with linear ramps.'
        )
    ),
]
# Pauses before rewriting a ramp whose beat 0 still holds the old value. Live applies
# a parameter change on its next update; slower plugins can take a little longer.
RAMP_SETTLE_DELAYS = (0.05, 0.2, 0.5)

RampStepLength = Annotated[
    float,
    Field(gt=0, le=4, description="Ramp resolution in beats; 0.0625 (1/16 beat) by default."),
]


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def write_automation(
    ctx: Context,
    track_index: TrackIndex,
    clip_index: ClipIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    points: AutomationPoints,
    mode: AutomationMode = "points",
    step_length: RampStepLength = 0.0625,
) -> str:
    """Write clip automation for one device parameter on a Session clip, replacing any
    existing envelope for that parameter on the clip.

    Times are in beats from the clip start. Values use the parameter's native range:
    read min and max with get_device_parameters first. Out-of-range values, bad times
    and over-long ramps are rejected before anything is written.

    mode="points" (default): each point becomes a step lasting its "duration" beats.
    Without a duration the step has zero length, which plays as a momentary spike,
    so give a duration to hold a value.

    mode="ramp": consecutive points are joined by a linear ramp, written as contiguous
    steps of about step_length beats, and the final value is reached by the ramp's
    end. Points must be in time order; two points at the same time make an instant
    jump. The last point holds until the clip end unless it has a duration; only the
    last point may have one. A ramp that starts at beat 0 also sets the parameter
    itself to the first value, so the clip does not jump from the old value at its
    start on every loop. Live applies that value on its next update, so the tool
    rewrites the ramp (up to three times, pausing briefly) until beat 0 reads the
    first value. Inside batch_commands it cannot wait: start_settled false in the
    result means beat 0 still jumps, so run write_automation again on its own.

    Example, a 4-bar filter sweep on a 4-bar clip:
      points=[{"time": 0, "value": 0.2}, {"time": 16, "value": 0.9}], mode="ramp"
    """
    connection = get_ableton_connection()
    params = {
        "track_index": track_index,
        "clip_index": clip_index,
        "device_index": device_index,
        "parameter": parameter,
        "points": points,
        "mode": mode,
        "step_length": step_length,
    }
    r = connection.send_command("write_automation", params)
    rewrites = 0
    for delay in RAMP_SETTLE_DELAYS:
        if r.get("start_settled") is not False:
            break
        time.sleep(delay)
        r = connection.send_command("write_automation", params)
        rewrites += 1
    if "step_count" not in r:
        return (
            f"Wrote {r.get('point_count')} automation points for {r.get('parameter')}, "
            "but the Remote Script running in Live is older than this server and ignores "
            "mode and duration. Run install, delete the script's __pycache__ and restart Live."
        )
    message = (
        f"Wrote {r['step_count']} steps from {r.get('point_count')} points ({r.get('mode')}) "
        f"for {r.get('parameter')} on {r.get('device')}, beats {r.get('start')} to {r.get('end')}"
    )
    if r.get("start_value_set") is not None:
        message += (
            f"; set {r.get('parameter')} to {r['start_value_set']} so beat 0 starts on the ramp"
        )
    if r.get("start_settled") is False:
        message += (
            f". Warning: after {rewrites} rewrites beat 0 still starts from the old value "
            "(a click on every loop); run write_automation again"
        )
    elif rewrites:
        message += f" (rewrote the ramp {rewrites}x while Live applied the new value)"
    return message


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
