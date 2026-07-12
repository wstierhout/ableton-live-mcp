"""Track lifecycle and mixer tools (volume, pan, mute, solo, sends)."""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import CrossfadeAssignment, PanValue, ToggleState, TrackIndex, TrackInsertIndex


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_track_info(ctx: Context, track_index: TrackIndex) -> str:
    """Return detailed state for one regular track without modifying the set.

    Includes name/type, mixer and arm/mute/solo state, sends, freeze/color state,
    playing/fired slot indices, every Session slot's basic clip state, and the
    device-chain names/types. Use get_session_info for global transport state,
    get_device_parameters for parameter values, or get_clip_info for full clip
    properties.
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("get_track_info", {"track_index": track_index})
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_midi_track(ctx: Context, index: int = -1) -> str:
    """
    Create a new MIDI track in the Ableton session.

    Parameters:
    - index: The index to insert the track at (-1 = end of list)
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("create_midi_track", {"index": index})
    return f"Created new MIDI track: {result.get('name', 'unknown')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_name(ctx: Context, track_index: int, name: str) -> str:
    """
    Set the name of a track.

    Parameters:
    - track_index: The index of the track to rename
    - name: The new name for the track
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("set_track_name", {"track_index": track_index, "name": name})
    return f"Renamed track to: {result.get('name', name)}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_track(ctx: Context, track_index: int) -> str:
    """Delete a track. NOTE: indices of all later tracks shift down by one."""
    result = get_ableton_connection().send_command("delete_track", {"track_index": track_index})
    return f"Track deleted. Remaining tracks: {result.get('track_count')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_volume(ctx: Context, track_index: int, volume: float) -> str:
    """Set track volume. Range 0.0-1.0 where 0.85 = 0 dB (unity gain)."""
    result = get_ableton_connection().send_command(
        "set_track_volume", {"track_index": track_index, "volume": volume}
    )
    return f"Track {track_index} volume set to {result.get('volume')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_track_pan(ctx: Context, track_index: TrackIndex, pan: PanValue) -> str:
    """Set one regular track's stereo pan position; 0.0 is centered.

    Live clamps the value to the track's native range. This changes mixer state,
    not the audio file or clip content. Use set_master_device_parameter for a
    device control, and set_crossfader for A/B crossfading.
    """
    result = get_ableton_connection().send_command(
        "set_track_pan", {"track_index": track_index, "pan": pan}
    )
    return f"Track {track_index} pan set to {result.get('panning')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_track_mute(ctx: Context, track_index: TrackIndex, mute: ToggleState) -> str:
    """Set one regular track's mute state (`true` mutes; `false` unmutes).

    Muting silences that track in Live but preserves its clips, devices, and
    mixer settings. Use set_track_solo to audition a track relative to the rest,
    or stop_clip when only Session clip playback should stop.
    """
    result = get_ableton_connection().send_command(
        "set_track_mute", {"track_index": track_index, "mute": mute}
    )
    return f"Track {track_index} mute: {result.get('mute')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_track_solo(ctx: Context, track_index: TrackIndex, solo: ToggleState) -> str:
    """Set one regular track's solo state (`true` solos; `false` unsolos).

    Solo changes monitoring relative to other tracks and does not alter clip or
    device content. Other tracks may remain soloed, so clear them separately when
    exclusive auditioning is required. Use set_track_mute to silence this track.
    """
    result = get_ableton_connection().send_command(
        "set_track_solo", {"track_index": track_index, "solo": solo}
    )
    return f"Track {track_index} solo: {result.get('solo')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_master_volume(ctx: Context, volume: float) -> str:
    """Set the Main/Master track volume. Range 0.0-1.0 where 0.85 = 0 dB."""
    result = get_ableton_connection().send_command("set_master_volume", {"volume": volume})
    return f"Master volume set to {result.get('volume')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_send(ctx: Context, track_index: int, send_index: int, value: float) -> str:
    """Set a track's send level (send_index 0 = Return A, 1 = Return B, ...). Range 0.0-1.0."""
    result = get_ableton_connection().send_command(
        "set_send", {"track_index": track_index, "send_index": send_index, "value": value}
    )
    return f"Track {track_index} send {send_index} set to {result.get('value')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_return_track(ctx: Context) -> str:
    """Create a new return track (a send bus, e.g. for shared reverb or delay)."""
    r = get_ableton_connection().send_command("create_return_track")
    return f"Return track created. Total returns: {r.get('return_track_count')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_return_track(ctx: Context, return_index: int) -> str:
    """Delete a return track by index (0 = Return A). Later returns shift down."""
    r = get_ableton_connection().send_command("delete_return_track", {"return_index": return_index})
    return f"Return track deleted. Total returns: {r.get('return_track_count')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def create_audio_track(ctx: Context, index: TrackInsertIndex = -1) -> str:
    """Insert an empty audio track at a chosen position, or append with `-1`.

    Non-negative indices insert before the current track at that position and
    shift it and later track indices up. Use create_midi_track for instruments
    and MIDI clips, or create_return_track for a shared send-effect bus.
    """
    r = get_ableton_connection().send_command("create_audio_track", {"index": index})
    return f"Audio track created. Total tracks: {r.get('track_count')}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_return_tracks(ctx: Context) -> str:
    """List return tracks (index + name)."""
    return json.dumps(get_ableton_connection().send_command("get_return_tracks"), indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_color(ctx: Context, track_index: int, color_index: int) -> str:
    """Set a track's color by index (0-69 in Live's palette)."""
    get_ableton_connection().send_command(
        "set_track_color", {"track_index": track_index, "color_index": color_index}
    )
    return f"Track {track_index} color set"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def duplicate_track(ctx: Context, track_index: TrackIndex) -> str:
    """Insert a copy of one regular track, including its devices and clips.

    This creates new content and increases the track count; later track indices
    shift to make room. Use duplicate_scene for a Session row or duplicate_clip_to
    for one clip. Rename or edit the copy to create a variation.
    """
    r = get_ableton_connection().send_command("duplicate_track", {"track_index": track_index})
    return f"Track duplicated. Total tracks: {r.get('track_count')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_arm(ctx: Context, track_index: int, arm: bool) -> str:
    """Arm or disarm a track for recording (fails on tracks that can't be armed,
    e.g. group/return tracks)."""
    result = get_ableton_connection().send_command(
        "set_track_arm", {"track_index": track_index, "arm": arm}
    )
    return f"Track {track_index} arm: {result.get('arm')}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_track_meters(ctx: Context, track_index: int) -> str:
    """Read a track's output level meters (post-fader): left/right 0.0-1.0ish
    (0.85 ≈ 0 dB, like faders). Poll during playback to judge balance/clipping -
    this is how you 'listen' without exporting."""

    r = get_ableton_connection().send_command("get_track_meters", {"track_index": track_index})
    return json.dumps(r)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_master_meters(ctx: Context) -> str:
    """Read the Master track's output meters - values pinned near 1.0 during
    playback mean the master is clipping; pull set_master_volume down."""

    r = get_ableton_connection().send_command("get_master_meters")
    return json.dumps(r)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_track_routing(ctx: Context, track_index: int) -> str:
    """Read a track's input/output routing (current + all available options)
    and monitoring state (0=In, 1=Auto, 2=Off). Routing options are identified
    by display_name - use those strings with set_track_routing."""

    r = get_ableton_connection().send_command("get_track_routing", {"track_index": track_index})
    return json.dumps(r, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_routing(ctx: Context, track_index: int, field: str, display_name: str) -> str:
    """Set track routing by display name. field: input_routing_type |
    output_routing_type | input_routing_channel | output_routing_channel.
    Enables sidechain sources, bus routing, and resampling (route a track's
    output into another track's input, arm, record). Get options from
    get_track_routing first."""
    r = get_ableton_connection().send_command(
        "set_track_routing",
        {"track_index": track_index, "field": field, "display_name": display_name},
    )
    return f"{field} = {r.get(field)}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_monitoring(ctx: Context, track_index: int, state: int) -> str:
    """Set input monitoring: 0=In (always hear input), 1=Auto, 2=Off.
    Required for resampling/bounce workflows."""
    r = get_ableton_connection().send_command(
        "set_track_monitoring", {"track_index": track_index, "state": state}
    )
    return f"Monitoring: {r.get('monitoring')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_crossfader(ctx: Context, value: float) -> str:
    """Set the master crossfader (-1.0 = full A, 0 = center, 1.0 = full B)."""
    r = get_ableton_connection().send_command("set_crossfader", {"value": value})
    return f"Crossfader: {r.get('crossfader')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_crossfade_assign(ctx: Context, track_index: TrackIndex, assign: CrossfadeAssignment) -> str:
    """Assign one regular track to crossfader side A, neither side, or side B.

    This configures which side affects the track; it does not move the crossfader
    or change track volume immediately. Use set_crossfader afterward to blend the
    A/B groups. Repeating the same assignment has no additional effect.
    """
    r = get_ableton_connection().send_command(
        "set_crossfade_assign", {"track_index": track_index, "assign": assign}
    )
    return f"Crossfade assign: {r.get('crossfade_assign')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_device(ctx: Context, track_index: int, device_index: int) -> str:
    """Delete a device from a track's chain by its position (0-based). Shifts the
    indices of later devices, so re-read the chain afterwards."""
    r = get_ableton_connection().send_command(
        "delete_device", {"track_index": track_index, "device_index": device_index}
    )
    return f"Deleted device; '{r.get('track')}' now has {r.get('device_count')} devices"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_take_lane(ctx: Context, track_index: int) -> str:
    """Add a take lane to a track (for comping multiple recorded takes on one
    track). Returns the new take-lane count."""
    r = get_ableton_connection().send_command("create_take_lane", {"track_index": track_index})
    return f"'{r.get('track')}' now has {r.get('take_lane_count')} take lanes"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_group_info(ctx: Context, track_index: int) -> str:
    """Report a track's group state: whether it is a foldable group track, whether
    it is inside a group, its fold state, and its parent group's name."""
    r = get_ableton_connection().send_command("get_group_info", {"track_index": track_index})
    return json.dumps(r, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_fold_state(ctx: Context, track_index: int, folded: bool = True) -> str:
    """Fold (collapse) or unfold a group track. Errors if the track is not a group
    track (see get_group_info)."""
    r = get_ableton_connection().send_command(
        "set_fold_state", {"track_index": track_index, "folded": folded}
    )
    return f"'{r.get('name')}' fold_state: {r.get('fold_state')}"
