"""Track lifecycle and mixer tools (volume, pan, mute, solo, sends)."""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_track_info(ctx: Context, track_index: int) -> str:
    """
    Get detailed information about a specific track in Ableton.

    Parameters:
    - track_index: The index of the track to get information about
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


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_pan(ctx: Context, track_index: int, pan: float) -> str:
    """Set track panning, -1.0 (left) to 1.0 (right)."""
    result = get_ableton_connection().send_command(
        "set_track_pan", {"track_index": track_index, "pan": pan}
    )
    return f"Track {track_index} pan set to {result.get('panning')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_mute(ctx: Context, track_index: int, mute: bool) -> str:
    """Mute or unmute a track."""
    result = get_ableton_connection().send_command(
        "set_track_mute", {"track_index": track_index, "mute": mute}
    )
    return f"Track {track_index} mute: {result.get('mute')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_track_solo(ctx: Context, track_index: int, solo: bool) -> str:
    """Solo or unsolo a track."""
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


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_audio_track(ctx: Context, index: int = -1) -> str:
    """Create a new audio track. index -1 appends at the end."""
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


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def duplicate_track(ctx: Context, track_index: int) -> str:
    """Duplicate a track along with its devices and clips."""
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


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_crossfade_assign(ctx: Context, track_index: int, assign: int) -> str:
    """Assign a track to the crossfader: 0=A, 1=none, 2=B."""
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
