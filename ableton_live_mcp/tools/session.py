"""Session, transport, tempo, scenes, locators, undo/redo tools."""

from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import (
    ArrangementBeat,
    DisplayName,
    OptionalDisplayName,
    PositiveBeatLength,
    SceneIndex,
    TimeSignatureDenominator,
    TimeSignatureNumerator,
    ToggleState,
    params,
)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_session_info(
    operation_id: Annotated[
        str | None,
        Field(description="Optional receipt ID from an uncertain command or batch error."),
    ] = None,
) -> dict:
    """Return global Live-set and transport state without modifying the set.

    Supply operation_id from an error to inspect a retained operation receipt.
    Includes tempo, song time signature, playback position/state, Arrangement
    loop, track/return/scene counts, scene names, key/scale, record mode, launch
    quantization, Master volume/pan, and Live version. Use get_track_info for a
    single track's clips/devices, or get_session_snapshot for a compact overview
    of every track.
    """
    ableton = get_ableton_connection()
    result = ableton.send_command("get_session_info")
    from .. import __version__

    result["server_version"] = __version__
    result["bridge_update_required"] = result.get("bridge_protocol", 1) < 2
    if operation_id:
        ableton.require_capability("operation_receipts")
        result["operation"] = ableton.send_command(
            "get_operation_status", {"operation_id": operation_id}
        )
    return result


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_tempo(tempo: float) -> dict:
    """
    Set the tempo of the Ableton session.

    Parameters:
    - tempo: The new tempo in BPM
    """
    ableton = get_ableton_connection()
    return ableton.send_command("set_tempo", {"tempo": tempo})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def start_playback() -> dict:
    """Start Arrangement playback; return requested/observed state and confirmation."""
    ableton = get_ableton_connection()
    ableton.require_capability("confirmed_transport")
    return ableton.send_command("start_playback")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def stop_playback() -> dict:
    """Stop playback; return requested/observed state after a later Live tick."""
    ableton = get_ableton_connection()
    ableton.require_capability("confirmed_transport")
    return ableton.send_command("stop_playback")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_scene(index: int = -1) -> dict:
    """Create a new scene (row of clip slots). index -1 appends at the end."""
    return get_ableton_connection().send_command("create_scene", {"index": index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def create_locator(time: ArrangementBeat, name: OptionalDisplayName = None) -> dict:
    """Create or rename an Arrangement locator at an absolute beat position.

    Requires stopped playback for exact placement. SIDE EFFECT: first moves the Arrangement playhead to `time`, because Live
    creates cues at the playhead. If a locator already exists there, it is reused
    and renamed when `name` is provided. Use set_arrangement_time when only the
    playhead should move, and get_locators to inspect existing markers.
    """
    conn = get_ableton_connection()
    conn.require_capability("confirmed_locator")
    return conn.send_command("create_locator", params(time=time, name=name))


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def fire_scene(scene_index: SceneIndex) -> dict:
    """Launch a Session-view scene using Live's launch quantization.

    This fires the row's clips together and can replace or stop clips currently
    playing on the affected tracks according to each slot's launch behavior.
    Use fire_clip to launch only one track's clip, or start_playback when the
    intent is Arrangement transport rather than Session launching.
    """
    return get_ableton_connection().send_command("fire_scene", {"scene_index": scene_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_scene_name(scene_index: SceneIndex, name: DisplayName) -> dict:
    """Rename one Session-view scene without changing any clips in the row.

    Use set_clip_name for an individual clip. Repeating the same name has no
    additional effect.
    """
    return get_ableton_connection().send_command(
        "set_scene_name", {"scene_index": scene_index, "name": name}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_time_signature(
    numerator: TimeSignatureNumerator,
    denominator: TimeSignatureDenominator,
) -> dict:
    """Set the Live set's global time signature, such as 4/4, 3/4, or 6/8.

    This changes bar/grid and metronome interpretation but does not rewrite clip
    notes. Use set_clip_signature instead when only one clip needs a different
    meter for polymeter. Repeating the same signature has no additional effect.
    """
    return get_ableton_connection().send_command(
        "set_time_signature", {"numerator": numerator, "denominator": denominator}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_loop(
    start: ArrangementBeat,
    length: PositiveBeatLength,
    enabled: ToggleState = True,
) -> dict:
    """Set the global Arrangement loop region and its enabled state.

    `start` is an absolute beat and `length` is a positive beat duration. The
    region is updated even when `enabled=false`; playback simply will not repeat
    it. Use set_clip_loop for a Session clip's loop braces, not the Arrangement.
    """
    return get_ableton_connection().send_command(
        "set_loop", {"start": start, "length": length, "enabled": enabled}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def undo() -> dict:
    """Revert the most recent undoable Live-set action.

    This mutates the set and may remove newly created content or restore deleted
    content. It has no target parameter: Live chooses the top undo-history entry.
    Use redo to reapply an accidental undo. Prefer batch_commands for related
    edits so one undo reverts the group.
    """
    return get_ableton_connection().send_command("undo")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def redo() -> dict:
    """Redo the last undone action in Live."""
    return get_ableton_connection().send_command("redo")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def batch_commands(commands: list[dict], operation_id: str | None = None) -> dict:
    """Preflight 1–128 commands, then execute in one undo group. No automatic rollback.
    Runtime errors include the failed step and completed results. Supply an
    operation_id to deduplicate retries within this bridge's last 256 mutation receipts.
    Read-only polling does not consume that window.
    An unknown/expired receipt is not permission to repeat an uncertain edit.
    Transport, locators, save, undo/redo, search_browser, get_session_notes and nested
    batches require standalone calls.
    A track command may include expected_track_id from get_session_snapshot to
    reject a stale index before it edits a different track.

    commands = [{"type": "<command>", "params": {...}}, ...]. Command names match
    the MCP tool names (the few that differ are auto-translated), e.g.
    [{"type": "set_tempo", "params": {"tempo": 80}},
     {"type": "create_midi_track", "params": {"index": -1}}].
    Composite tools (record_section, load_drum_kit, apply_recipe, save_set, the
    generate_* tools) are NOT batchable - they orchestrate multiple commands
    server-side. Prefer this for multi-step edits: fewer round-trips, one undo
    reverts all.
    """
    conn = get_ableton_connection()
    conn.require_capability("batch_preflight")
    if isinstance(commands, list):
        for command in commands:
            name = command.get("type") if isinstance(command, dict) else None
            if name == "write_automation":
                conn.require_capability("automation_v2")
            elif name == "read_automation":
                conn.require_capability("automation_readback")
    return conn.send_command("batch", {"commands": commands}, operation_id=operation_id)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def capture_midi() -> dict:
    """Grab recently played MIDI into a new clip (Live's Capture MIDI). Requires
    that MIDI was recently played into an armed/monitored track."""
    return get_ableton_connection().send_command("capture_midi")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_song_scale(root_note: int | None = None, scale_name: str | None = None) -> dict:
    """Set the song's key context. root_note 0-11 (0=C, 2=D...), scale_name e.g.
    "Major", "Minor", "Dorian". Read current values via get_session_info."""
    if root_note is None and scale_name is None:
        raise ValueError("Provide root_note and/or scale_name")
    return get_ableton_connection().send_command(
        "set_song_scale", params(root_note=root_note, scale_name=scale_name)
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_metronome(enabled: bool) -> dict:
    """Turn Live's metronome click on/off."""
    return get_ableton_connection().send_command("set_metronome", {"enabled": enabled})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def stop_all_clips() -> dict:
    """Stop every playing Session clip (does not stop the transport)."""
    return get_ableton_connection().send_command("stop_all_clips")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def back_to_arranger() -> dict:
    """Return playback control to the Arrangement (after Session clips have
    overridden arrangement content - the orange 'Back to Arrangement' state)."""
    return get_ableton_connection().send_command("back_to_arranger")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_record_mode(enabled: ToggleState) -> dict:
    """Arm or disarm Live's global Arrangement Record state.

    `true` enables Arrangement recording; `false` disables it. Enabling alone
    does not record: arm the intended tracks with set_track_arm, then use
    start_playback. Use set_session_record or trigger_session_record for Session
    slots instead. Repeating the current state has no additional effect.
    """
    return get_ableton_connection().send_command("set_record_mode", {"enabled": enabled})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_session_record(enabled: bool) -> dict:
    """Toggle Session record (records into armed tracks' selected Session slots;
    also enables MIDI overdub into playing clips)."""
    return get_ableton_connection().send_command("set_session_record", {"enabled": enabled})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def continue_playing() -> dict:
    """Resume playback from the current position (start_playback restarts from
    the playhead's last start point instead)."""
    return get_ableton_connection().send_command("continue_playing")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_clip_trigger_quantization(value: int) -> dict:
    """Global launch quantization for firing clips/scenes. 0=None (instant,
    good for auditioning), 4=1 bar (default), 7=1/4 note, 13=1/32."""
    return get_ableton_connection().send_command("set_clip_trigger_quantization", {"value": value})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_scene(scene_index: int) -> dict:
    """Delete a scene (row). Later scene indices shift down by one."""
    return get_ableton_connection().send_command("delete_scene", {"scene_index": scene_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def duplicate_scene(scene_index: SceneIndex) -> dict:
    """Insert a copy of one Session scene, including every clip in its row.

    This creates new content and increases the scene count; later scene indices
    shift to make room. Use duplicate_track for a whole track or duplicate_clip_to
    for one clip. Use the new scene as an editable section variation.
    """
    return get_ableton_connection().send_command("duplicate_scene", {"scene_index": scene_index})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def capture_and_insert_scene() -> dict:
    """Snapshot the currently playing Session clips into a new scene - the
    'keep that combination' workflow."""
    return get_ableton_connection().send_command("capture_and_insert_scene")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_locators() -> dict:
    """List arrangement locators (index, name, beat time) for structure-aware
    navigation. Jump with jump_to_locator."""

    return get_ableton_connection().send_command("get_locators")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def jump_to_locator(locator_index: int) -> dict:
    """Move the playhead to a locator (index from get_locators)."""
    return get_ableton_connection().send_command(
        "jump_to_locator", {"locator_index": locator_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def re_enable_automation() -> dict:
    """Restore automation that was overridden by manual parameter changes.
    IMPORTANT: setting a device parameter that has an envelope overrides the
    envelope until this is called."""
    return get_ableton_connection().send_command("re_enable_automation")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_arrangement_overdub(enabled: bool) -> dict:
    """Toggle arrangement overdub: recording layers MIDI onto existing
    arrangement clips instead of replacing them."""
    return get_ableton_connection().send_command("set_arrangement_overdub", {"enabled": enabled})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_session_automation_record(enabled: bool) -> dict:
    """Toggle recording of parameter changes into Session clip envelopes -
    an alternative to write_automation for captured knob rides."""
    return get_ableton_connection().send_command(
        "set_session_automation_record", {"enabled": enabled}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def trigger_session_record(record_length: float | None = None) -> dict:
    """Start Session recording into armed tracks; record_length in beats records
    a fixed-length loop (e.g. 16.0 = 4 bars) then auto-switches to playback."""
    return get_ableton_connection().send_command(
        "trigger_session_record", params(record_length=record_length)
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def tap_tempo() -> dict:
    """Tap the tempo once. Tapping repeatedly at a beat sets the tempo to that
    rate; a single tap nudges the transport. Returns the resulting tempo."""
    return get_ableton_connection().send_command("tap_tempo")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_groove_amount(amount: float) -> dict:
    """Set the global Groove Amount (0.0 to 1.0), which scales how strongly every
    clip's assigned groove is applied across the whole set."""
    return get_ableton_connection().send_command("set_groove_amount", {"amount": amount})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_swing_amount(amount: float) -> dict:
    """Set the global swing amount (0.0 to 1.0) used when quantizing with swing."""
    return get_ableton_connection().send_command("set_swing_amount", {"amount": amount})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def jump_by(beats: float) -> dict:
    """Move the playhead by a relative number of beats (negative moves back).
    Works during playback for on-the-fly navigation."""
    return get_ableton_connection().send_command("jump_by", {"beats": beats})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def jump_to_cue(direction: int = 1) -> dict:
    """Jump the playhead to the next locator/cue (direction >= 0) or the previous
    one (direction < 0)."""
    return get_ableton_connection().send_command("jump_to_cue", {"direction": direction})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_ableton_link(enabled: bool) -> dict:
    """Enable or disable Ableton Link, which syncs tempo and phase with other
    Link-enabled apps and devices on the local network."""
    return get_ableton_connection().send_command("set_ableton_link", {"enabled": enabled})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_session_snapshot() -> dict:
    """One-call overview of the whole set: tempo, time signature, play state, and
    for every track its name, type, mute/solo/arm, volume, clip count, and device
    names, plus return-track names. Prefer this over many get_track_info calls."""
    return get_ableton_connection().send_command("get_session_snapshot")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_scale_info() -> dict:
    """Read the song's scale and tuning: scale name, root note, scale intervals, and
    the active tuning system (microtonal, Live 12.3+) if any. Set the scale with
    set_song_scale; tune device parameters (e.g. Wavetable) with set_device_parameter."""
    return get_ableton_connection().send_command("get_scale_info")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def save_set() -> dict:
    """Save the current Live set to its existing file, if the Live API supports it
    (it does not on most versions). Returns whether the API call is available and
    whether the save succeeded. To save under a new name/path, use the GUI Save-As."""
    return get_ableton_connection().send_command("try_save_project")
