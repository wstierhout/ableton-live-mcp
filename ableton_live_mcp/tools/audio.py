"""Capture internal audio to a file without the GUI Export dialog.

`record_section` uses only the Live API: it creates a temporary audio track,
routes its input to the master (Resampling) or another track, arms it, and records
the arrangement over a time range in real time. The resulting clip exposes its WAV
path, which the closed-loop analysis (MusicGen/tools/analyze_refs.py) can read - so
the agent can hear an internal signal-chain point, not just the final master export.
"""

import json
import time

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection


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
    in REAL TIME (a 4-bar section takes about 4 bars of wall-clock). Returns the
    recorded WAV path; analyze it with MusicGen/tools/analyze_refs.py. Set
    cleanup=False to keep the recording track. Runs the transport and adds an
    arrangement clip."""
    if end_beat <= start_beat:
        raise ValueError("end_beat must be greater than start_beat")
    conn = get_ableton_connection()
    tempo = conn.send_command("get_session_info").get("tempo", 120.0)

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
        conn.send_command("set_track_arm", {"track_index": track_index, "arm": True})
        conn.send_command("set_current_song_time", {"time": start_beat})
        conn.send_command("set_record_mode", {"enabled": True})
        conn.send_command("start_playback")
        time.sleep((end_beat - start_beat) * 60.0 / tempo + 0.4)
        conn.send_command("set_record_mode", {"enabled": False})
        conn.send_command("stop_playback")

        clips = conn.send_command("get_arrangement_clips", {"track_index": track_index}).get(
            "clips", []
        )
        recorded = [c for c in clips if c.get("is_audio_clip") and c.get("file_path")]
        result["file_path"] = recorded[-1]["file_path"] if recorded else None
        result["note"] = (
            "Analyze with: uv run --with numpy python3 tools/analyze_refs.py <file_path>"
            if result["file_path"]
            else "No recorded file found - check that the track armed and input routing accepted the source."
        )
    finally:
        if cleanup:
            conn.send_command("delete_track", {"track_index": track_index})
            result["cleaned_up"] = True
    return json.dumps(result, indent=2)
