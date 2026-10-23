"""Track lifecycle and mixer tools (volume, pan, mute, solo, sends)."""

from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import CrossfadeAssignment, PanValue, ToggleState, TrackIndex, TrackInsertIndex


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_track_info(track_index: TrackIndex) -> dict:
    """Return detailed state for one regular track without modifying the set.

    Includes name/type, mixer and arm/mute/solo state, sends, freeze/color state,
    playing/fired slot indices, every Session slot's basic clip state, and the
    device-chain names/types. Use get_session_info for global transport state,
    get_device_parameters for parameter values, or get_clip_info for full clip
    properties.
    """
    ableton = get_ableton_connection()
    return ableton.send_command("get_track_info", {"track_index": track_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_midi_track(index: int = -1) -> dict:
    """
    Create a new MIDI track in the Ableton session.

    Parameters:
    - index: The index to insert the track at (-1 = end of list)
    """
    ableton = get_ableton_connection()
    return ableton.send_command("create_midi_track", {"index": index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_name(track_index: int, name: str) -> dict:
    """
    Set the name of a track.

    Parameters:
    - track_index: The index of the track to rename
    - name: The new name for the track
    """
    ableton = get_ableton_connection()
    return ableton.send_command("set_track_name", {"track_index": track_index, "name": name})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_track(track_index: int) -> dict:
    """Delete a track. NOTE: indices of all later tracks shift down by one."""
    return get_ableton_connection().send_command("delete_track", {"track_index": track_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_volume(track_index: int, volume: float) -> dict:
    """Set track volume. Range 0.0-1.0 where 0.85 = 0 dB (unity gain)."""
    return get_ableton_connection().send_command(
        "set_track_volume", {"track_index": track_index, "volume": volume}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_track_pan(track_index: TrackIndex, pan: PanValue) -> dict:
    """Set one regular track's stereo pan position; 0.0 is centered.

    Live clamps the value to the track's native range. This changes mixer state,
    not the audio file or clip content. Use set_master_device_parameter for a
    device control, and set_crossfader for A/B crossfading.
    """
    return get_ableton_connection().send_command(
        "set_track_pan", {"track_index": track_index, "pan": pan}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_track_mute(track_index: TrackIndex, mute: ToggleState) -> dict:
    """Set one regular track's mute state (`true` mutes; `false` unmutes).

    Muting silences that track in Live but preserves its clips, devices, and
    mixer settings. Use set_track_solo to audition a track relative to the rest,
    or stop_clip when only Session clip playback should stop.
    """
    return get_ableton_connection().send_command(
        "set_track_mute", {"track_index": track_index, "mute": mute}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_track_solo(track_index: TrackIndex, solo: ToggleState) -> dict:
    """Set one regular track's solo state (`true` solos; `false` unsolos).

    Solo changes monitoring relative to other tracks and does not alter clip or
    device content. Other tracks may remain soloed, so clear them separately when
    exclusive auditioning is required. Use set_track_mute to silence this track.
    """
    return get_ableton_connection().send_command(
        "set_track_solo", {"track_index": track_index, "solo": solo}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_master_volume(volume: float) -> dict:
    """Set the Main/Master track volume. Range 0.0-1.0 where 0.85 = 0 dB."""
    return get_ableton_connection().send_command("set_master_volume", {"volume": volume})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_send(track_index: int, send_index: int, value: float) -> dict:
    """Set a track's send level (send_index 0 = Return A, 1 = Return B, ...). Range 0.0-1.0."""
    return get_ableton_connection().send_command(
        "set_send", {"track_index": track_index, "send_index": send_index, "value": value}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_return_track() -> dict:
    """Create a new return track (a send bus, e.g. for shared reverb or delay)."""
    return get_ableton_connection().send_command("create_return_track")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_return_track(return_index: int) -> dict:
    """Delete a return track by index (0 = Return A). Later returns shift down."""
    return get_ableton_connection().send_command(
        "delete_return_track", {"return_index": return_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def create_audio_track(index: TrackInsertIndex = -1) -> dict:
    """Insert an empty audio track at a chosen position, or append with `-1`.

    Non-negative indices insert before the current track at that position and
    shift it and later track indices up. Use create_midi_track for instruments
    and MIDI clips, or create_return_track for a shared send-effect bus.
    """
    return get_ableton_connection().send_command("create_audio_track", {"index": index})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_return_tracks() -> dict:
    """List return tracks (index + name)."""
    return get_ableton_connection().send_command("get_return_tracks")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_color(track_index: int, color_index: int) -> dict:
    """Set a track's color by index (0-69 in Live's palette)."""
    return get_ableton_connection().send_command(
        "set_track_color", {"track_index": track_index, "color_index": color_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def duplicate_track(track_index: TrackIndex) -> dict:
    """Insert a copy of one regular track, including its devices and clips.

    This creates new content and increases the track count; later track indices
    shift to make room. Use duplicate_scene for a Session row or duplicate_clip_to
    for one clip. Rename or edit the copy to create a variation.
    """
    return get_ableton_connection().send_command("duplicate_track", {"track_index": track_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_arm(track_index: int, arm: bool) -> dict:
    """Arm or disarm a track for recording (fails on tracks that can't be armed,
    e.g. group/return tracks)."""
    return get_ableton_connection().send_command(
        "set_track_arm", {"track_index": track_index, "arm": arm}
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_track_meters(track_index: int) -> dict:
    """Read Live's normalized, smoothed output meters (0–1) for a track.
    These are meter readings, not calibrated dBFS or proof of clipping.
    Use analyze_audio_file for measurements of exported samples."""

    return get_ableton_connection().send_command("get_track_meters", {"track_index": track_index})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_master_meters() -> dict:
    """Read Live's normalized, smoothed Main/Master output meters (0–1).
    A reading near 1 does not by itself establish clipping in rendered audio."""

    return get_ableton_connection().send_command("get_master_meters")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_track_routing(track_index: int) -> dict:
    """Read a track's input/output routing (current + all available options)
    and monitoring state (0=In, 1=Auto, 2=Off). Routing options are identified
    by display_name - use those strings with set_track_routing."""

    return get_ableton_connection().send_command("get_track_routing", {"track_index": track_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_routing(track_index: int, field: str, display_name: str) -> dict:
    """Set track routing by display name. field: input_routing_type |
    output_routing_type | input_routing_channel | output_routing_channel.
    Enables sidechain sources, bus routing, and resampling (route a track's
    output into another track's input, arm, record). Get options from
    get_track_routing first."""
    return get_ableton_connection().send_command(
        "set_track_routing",
        {"track_index": track_index, "field": field, "display_name": display_name},
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_monitoring(track_index: int, state: int) -> dict:
    """Set input monitoring: 0=In (always hear input), 1=Auto, 2=Off.
    Required for resampling/bounce workflows."""
    return get_ableton_connection().send_command(
        "set_track_monitoring", {"track_index": track_index, "state": state}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_crossfader(value: float) -> dict:
    """Set the master crossfader (-1.0 = full A, 0 = center, 1.0 = full B)."""
    return get_ableton_connection().send_command("set_crossfader", {"value": value})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_crossfade_assign(track_index: TrackIndex, assign: CrossfadeAssignment) -> dict:
    """Assign one regular track to crossfader side A, neither side, or side B.

    This configures which side affects the track; it does not move the crossfader
    or change track volume immediately. Use set_crossfader afterward to blend the
    A/B groups. Repeating the same assignment has no additional effect.
    """
    return get_ableton_connection().send_command(
        "set_crossfade_assign", {"track_index": track_index, "assign": assign}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_device(track_index: int, device_index: int) -> dict:
    """Delete a device from a track's chain by its position (0-based). Shifts the
    indices of later devices, so re-read the chain afterwards."""
    return get_ableton_connection().send_command(
        "delete_device", {"track_index": track_index, "device_index": device_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_take_lane(track_index: int) -> dict:
    """Add a take lane to a track (for comping multiple recorded takes on one
    track). Returns the new take-lane count."""
    return get_ableton_connection().send_command("create_take_lane", {"track_index": track_index})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_group_info(track_index: int) -> dict:
    """Report a track's group state: whether it is a foldable group track, whether
    it is inside a group, its fold state, and its parent group's name."""
    return get_ableton_connection().send_command("get_group_info", {"track_index": track_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_fold_state(track_index: int, folded: bool = True) -> dict:
    """Fold (collapse) or unfold a group track. Errors if the track is not a group
    track (see get_group_info)."""
    return get_ableton_connection().send_command(
        "set_fold_state", {"track_index": track_index, "folded": folded}
    )
