"""Capture internal audio to a file without the GUI Export dialog.

`record_section` uses only the Live API: it creates a temporary audio track,
routes its input to the master (Resampling) or another track, arms it, and records
the arrangement over a time range in real time. The resulting clip exposes its WAV
path so the agent can hear an internal signal-chain point, not just the final
master export.
"""

import json
import time

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection

# Real-time recording blocks a server worker for its whole duration; refuse
# anything that would sleep unreasonably long (a typo'd end_beat at 82 BPM can
# otherwise block for hours).
MAX_RECORD_SECONDS = 300.0


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def record_section(
    ctx: Context,
    start_beat: float,
    end_beat: float,
    source: str = "Resampling",
    cleanup: bool = True,
) -> str:
    """Bounce a section of the arrangement to an audio file WITHOUT the Export
    dialog, so you can hear an internal signal. Creates a temporary audio track,
    routes its input to `source` ("Resampling" = the master output, or a track's
    name for that track's output), arms it, and records from start_beat to end_beat
    in REAL TIME (a 4-bar section takes about 4 bars of wall-clock; capped at
    5 minutes). Returns the recorded WAV path. Set cleanup=False to keep the
    recording track. Runs the transport and adds an arrangement clip."""
    if end_beat <= start_beat:
        raise ValueError("end_beat must be greater than start_beat")
    conn = get_ableton_connection()
    tempo = conn.send_command("get_session_info").get("tempo", 120.0)
    duration = (end_beat - start_beat) * 60.0 / tempo
    if duration > MAX_RECORD_SECONDS:
        raise ValueError(
            f"Section is {duration:.0f}s of real-time recording at {tempo:g} BPM; "
            f"the cap is {MAX_RECORD_SECONDS:.0f}s. Record a shorter range."
        )

    created = conn.send_command("create_audio_track", {"index": -1})
    # create_audio_track appends at the end; the new track is the last regular track.
    track_index = created.get("index")
    if track_index is None and created.get("track_count"):
        track_index = created["track_count"] - 1
    if track_index is None:
        raise RuntimeError("Could not create an audio track for the bounce")
    result = {"track_index": track_index, "source": source}
    try:
        conn.send_command("set_track_name", {"track_index": track_index, "name": "Bounce"})
        conn.send_command(
            "set_track_routing",
            {"track_index": track_index, "field": "input_routing_type", "display_name": source},
        )
        # Monitoring must be OFF before arming: Live's default (Auto) feeds the
        # monitored input back to the master while armed - a feedback loop that
        # overdrives the bounce when recording Resampling.
        conn.send_command("set_track_monitoring", {"track_index": track_index, "state": 2})
        conn.send_command("set_track_arm", {"track_index": track_index, "arm": True})
        conn.send_command("set_current_song_time", {"time": start_beat})
        conn.send_command("set_record_mode", {"enabled": True})
        conn.send_command("start_playback")
        time.sleep(duration + 0.4)
        conn.send_command("set_record_mode", {"enabled": False})
        conn.send_command("stop_playback")

        clips = conn.send_command("get_arrangement_clips", {"track_index": track_index}).get(
            "clips", []
        )
        recorded = [c for c in clips if c.get("is_audio_clip") and c.get("file_path")]
        result["file_path"] = recorded[-1]["file_path"] if recorded else None
        if not result["file_path"]:
            result["note"] = (
                "No recorded file found - check that the track armed and input "
                "routing accepted the source."
            )
    finally:
        if cleanup:
            try:
                conn.send_command("delete_track", {"track_index": track_index})
                result["cleaned_up"] = True
            except Exception as cleanup_err:
                # Don't mask an in-flight error with a cleanup failure.
                result["cleaned_up"] = False
                result["cleanup_error"] = str(cleanup_err)
    return json.dumps(result, indent=2)
