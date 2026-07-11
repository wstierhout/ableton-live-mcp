"""Session, transport, tempo, scenes, locators, undo/redo tools."""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import params


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_session_info(ctx: Context) -> str:
    """Get detailed information about the current Ableton session

    Parameters:
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("get_session_info")
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_tempo(ctx: Context, tempo: float) -> str:
    """
    Set the tempo of the Ableton session.

    Parameters:
    - tempo: The new tempo in BPM
    """
    ableton = get_ableton_connection()
    ableton.send_command("set_tempo", {"tempo": tempo})
    return f"Set tempo to {tempo} BPM"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def start_playback(ctx: Context) -> str:
    """Start Arrangement playback from the current song time. NOTE: the response
    reports the PRE-command state (may say playing=false right after starting) -
    confirm with get_session_info.
    """
    ableton = get_ableton_connection()
    ableton.send_command("start_playback")
    return "Started playback"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def stop_playback(ctx: Context) -> str:
    """Stop playback. NOTE: the response reports the PRE-command state - confirm
    with get_session_info.
    """
    ableton = get_ableton_connection()
    ableton.send_command("stop_playback")
    return "Stopped playback"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_scene(ctx: Context, index: int = -1) -> str:
    """Create a new scene (row of clip slots). index -1 appends at the end."""
    result = get_ableton_connection().send_command("create_scene", {"index": index})
    return f"Scene created. Total scenes: {result.get('scene_count')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_locator(ctx: Context, time: float, name: str | None = None) -> str:
    """Create an arrangement locator (marker) at a beat time, optionally named."""
    conn = get_ableton_connection()
    # Move the playhead first (separate command) so it settles before the cue is
    # placed - set_or_delete_cue reads the live playhead position.
    conn.send_command("set_current_song_time", {"time": time})
    r = conn.send_command("create_locator", params(name=name))
    return f"Locator '{r.get('name')}' at beat {r.get('time')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def fire_scene(ctx: Context, scene_index: int) -> str:
    """Launch a scene (fires every clip in that row)."""
    get_ableton_connection().send_command("fire_scene", {"scene_index": scene_index})
    return f"Fired scene {scene_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_scene_name(ctx: Context, scene_index: int, name: str) -> str:
    """Rename a scene."""
    get_ableton_connection().send_command(
        "set_scene_name", {"scene_index": scene_index, "name": name}
    )
    return f"Scene {scene_index} named '{name}'"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_time_signature(ctx: Context, numerator: int, denominator: int) -> str:
    """Set the song time signature (e.g. 4/4, 3/4, 6/8)."""
    r = get_ableton_connection().send_command(
        "set_time_signature", {"numerator": numerator, "denominator": denominator}
    )
    return f"Time signature {r.get('numerator')}/{r.get('denominator')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_loop(ctx: Context, start: float, length: float, enabled: bool = True) -> str:
    """Set the arrangement loop region (in beats) and enable/disable looping."""
    r = get_ableton_connection().send_command(
        "set_loop", {"start": start, "length": length, "enabled": enabled}
    )
    return f"Loop {r.get('loop_start')}..+{r.get('loop_length')} enabled={r.get('loop')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def undo(ctx: Context) -> str:
    """Undo the last action in Live."""
    get_ableton_connection().send_command("undo")
    return "Undone"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def redo(ctx: Context) -> str:
    """Redo the last undone action in Live."""
    get_ableton_connection().send_command("redo")
    return "Redone"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def batch_commands(ctx: Context, commands: list[dict]) -> str:
    """Run several commands in ONE round-trip and ONE undo step (atomic-ish:
    stops at the first error; completed steps are a single undo away).

    commands = [{"type": "<command>", "params": {...}}, ...]. Command names match
    the MCP tool names (the few that differ are auto-translated), e.g.
    [{"type": "set_tempo", "params": {"tempo": 80}},
     {"type": "create_midi_track", "params": {"index": -1}}].
    Prefer this for multi-step edits: fewer round-trips, one undo reverts all.
    """
    result = get_ableton_connection().send_command("batch", {"commands": commands})
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def capture_midi(ctx: Context) -> str:
    """Grab recently played MIDI into a new clip (Live's Capture MIDI). Requires
    that MIDI was recently played into an armed/monitored track."""
    get_ableton_connection().send_command("capture_midi")
    return "Captured recent MIDI"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_song_scale(
    ctx: Context, root_note: int | None = None, scale_name: str | None = None
) -> str:
    """Set the song's key context. root_note 0-11 (0=C, 2=D...), scale_name e.g.
    "Major", "Minor", "Dorian". Read current values via get_session_info."""
    result = get_ableton_connection().send_command(
        "set_song_scale", params(root_note=root_note, scale_name=scale_name)
    )
    return f"Scale set: {json.dumps(result)}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_metronome(ctx: Context, enabled: bool) -> str:
    """Turn Live's metronome click on/off."""
    r = get_ableton_connection().send_command("set_metronome", {"enabled": enabled})
    return f"Metronome: {r.get('metronome')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def stop_all_clips(ctx: Context) -> str:
    """Stop every playing Session clip (does not stop the transport)."""
    get_ableton_connection().send_command("stop_all_clips")
    return "All session clips stopped"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def back_to_arranger(ctx: Context) -> str:
    """Return playback control to the Arrangement (after Session clips have
    overridden arrangement content - the orange 'Back to Arrangement' state)."""
    get_ableton_connection().send_command("back_to_arranger")
    return "Playback control returned to the Arrangement"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_record_mode(ctx: Context, enabled: bool) -> str:
    """Toggle Arrangement record. With record_mode on, start_playback records
    armed tracks into the Arrangement."""
    r = get_ableton_connection().send_command("set_record_mode", {"enabled": enabled})
    return f"Arrangement record: {r.get('record_mode')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_session_record(ctx: Context, enabled: bool) -> str:
    """Toggle Session record (records into armed tracks' selected Session slots;
    also enables MIDI overdub into playing clips)."""
    r = get_ableton_connection().send_command("set_session_record", {"enabled": enabled})
    return f"Session record: {r.get('session_record')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def continue_playing(ctx: Context) -> str:
    """Resume playback from the current position (start_playback restarts from
    the playhead's last start point instead)."""
    get_ableton_connection().send_command("continue_playing")
    return "Playback continued"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_trigger_quantization(ctx: Context, value: int) -> str:
    """Global launch quantization for firing clips/scenes. 0=None (instant,
    good for auditioning), 4=1 bar (default), 7=1/4 note, 13=1/32."""
    r = get_ableton_connection().send_command("set_clip_trigger_quantization", {"value": value})
    return f"Clip trigger quantization: {r.get('clip_trigger_quantization')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_scene(ctx: Context, scene_index: int) -> str:
    """Delete a scene (row). Later scene indices shift down by one."""
    r = get_ableton_connection().send_command("delete_scene", {"scene_index": scene_index})
    return f"Scene deleted. Remaining: {r.get('scene_count')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def duplicate_scene(ctx: Context, scene_index: int) -> str:
    """Duplicate a scene with all its clips - instant section variation."""
    r = get_ableton_connection().send_command("duplicate_scene", {"scene_index": scene_index})
    return f"Scene duplicated. Total: {r.get('scene_count')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def capture_and_insert_scene(ctx: Context) -> str:
    """Snapshot the currently playing Session clips into a new scene - the
    'keep that combination' workflow."""
    r = get_ableton_connection().send_command("capture_and_insert_scene")
    return f"Scene captured. Total: {r.get('scene_count')}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_locators(ctx: Context) -> str:
    """List arrangement locators (index, name, beat time) for structure-aware
    navigation. Jump with jump_to_locator."""

    return json.dumps(get_ableton_connection().send_command("get_locators"), indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def jump_to_locator(ctx: Context, locator_index: int) -> str:
    """Move the playhead to a locator (index from get_locators)."""
    r = get_ableton_connection().send_command("jump_to_locator", {"locator_index": locator_index})
    return f"Jumped to '{r.get('jumped_to')}' at beat {r.get('time')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def re_enable_automation(ctx: Context) -> str:
    """Restore automation that was overridden by manual parameter changes.
    IMPORTANT: setting a device parameter that has an envelope overrides the
    envelope until this is called."""
    get_ableton_connection().send_command("re_enable_automation")
    return "Automation re-enabled"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_arrangement_overdub(ctx: Context, enabled: bool) -> str:
    """Toggle arrangement overdub: recording layers MIDI onto existing
    arrangement clips instead of replacing them."""
    r = get_ableton_connection().send_command("set_arrangement_overdub", {"enabled": enabled})
    return f"Arrangement overdub: {r.get('arrangement_overdub')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_session_automation_record(ctx: Context, enabled: bool) -> str:
    """Toggle recording of parameter changes into Session clip envelopes -
    an alternative to write_automation for captured knob rides."""
    r = get_ableton_connection().send_command("set_session_automation_record", {"enabled": enabled})
    return f"Session automation record: {r.get('session_automation_record')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def trigger_session_record(ctx: Context, record_length: float | None = None) -> str:
    """Start Session recording into armed tracks; record_length in beats records
    a fixed-length loop (e.g. 16.0 = 4 bars) then auto-switches to playback."""
    get_ableton_connection().send_command(
        "trigger_session_record", params(record_length=record_length)
    )
    return f"Session record triggered ({record_length or 'unbounded'} beats)"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def tap_tempo(ctx: Context) -> str:
    """Tap the tempo once. Tapping repeatedly at a beat sets the tempo to that
    rate; a single tap nudges the transport. Returns the resulting tempo."""
    r = get_ableton_connection().send_command("tap_tempo")
    return f"Tapped tempo; now {r.get('tempo')} BPM"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_groove_amount(ctx: Context, amount: float) -> str:
    """Set the global Groove Amount (0.0 to 1.0), which scales how strongly every
    clip's assigned groove is applied across the whole set."""
    r = get_ableton_connection().send_command("set_groove_amount", {"amount": amount})
    return f"Global groove amount: {r.get('groove_amount')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_swing_amount(ctx: Context, amount: float) -> str:
    """Set the global swing amount (0.0 to 1.0) used when quantizing with swing."""
    r = get_ableton_connection().send_command("set_swing_amount", {"amount": amount})
    return f"Global swing amount: {r.get('swing_amount')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def jump_by(ctx: Context, beats: float) -> str:
    """Move the playhead by a relative number of beats (negative moves back).
    Works during playback for on-the-fly navigation."""
    r = get_ableton_connection().send_command("jump_by", {"beats": beats})
    return f"Playhead at beat {r.get('current_song_time')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def jump_to_cue(ctx: Context, direction: int = 1) -> str:
    """Jump the playhead to the next locator/cue (direction >= 0) or the previous
    one (direction < 0)."""
    r = get_ableton_connection().send_command("jump_to_cue", {"direction": direction})
    return f"Playhead at beat {r.get('current_song_time')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_ableton_link(ctx: Context, enabled: bool) -> str:
    """Enable or disable Ableton Link, which syncs tempo and phase with other
    Link-enabled apps and devices on the local network."""
    r = get_ableton_connection().send_command("set_ableton_link", {"enabled": enabled})
    return f"Ableton Link enabled: {r.get('ableton_link_enabled')}"
