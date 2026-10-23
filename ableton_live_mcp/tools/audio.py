"""Capture internal audio to a file without the GUI Export dialog.

`record_section` uses only the Live API: it creates a temporary audio track,
routes its input to the master (Resampling) or another track, arms it, and records
the arrangement over a time range in real time. The resulting clip exposes its WAV
path so the agent can hear an internal signal-chain point, not just the final
master export.
"""

import math
import time
import uuid

from mcp.types import ToolAnnotations

from ..app import mcp
from ..cancellation import cancellable_wait, cleanup_scope
from ..connection import get_ableton_connection

# Real-time recording blocks a server worker for its whole duration; refuse
# anything that would sleep unreasonably long (a typo'd end_beat at 82 BPM can
# otherwise block for hours).
MAX_RECORD_SECONDS = 300.0


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def record_section(
    start_beat: float,
    end_beat: float,
    source: str = "Resampling",
    cleanup: bool = True,
) -> dict:
    """Bounce a section of the arrangement to an audio file WITHOUT the Export
    dialog, so you can hear an internal signal. Creates a temporary audio track,
    routes its input to `source` ("Resampling" = the master output, or a track's
    name for that track's output), arms it, and records from start_beat to end_beat
    in REAL TIME (a 4-bar section takes about 4 bars of wall-clock; capped at
    5 minutes). Returns the recorded WAV path. Set cleanup=False to keep the
    recording track. Requires stopped transport and other tracks disarmed.
    Cancellation disarms the owned track and attempts transport recovery; the
    cleanup leaves the playhead where Live or the user last positioned it."""
    if (
        not all(math.isfinite(x) for x in (start_beat, end_beat))
        or start_beat < 0
        or end_beat <= start_beat
    ):
        raise ValueError("Use finite beats with 0 <= start_beat < end_beat")
    conn = get_ableton_connection()
    conn.require_capability("stable_track_guards")
    conn.require_capability("confirmed_transport")
    before = conn.send_command("get_session_info")
    if before.get("is_playing") or before.get("record_mode"):
        raise ValueError("Stop playback and recording before bouncing a section")

    def check_loop(state):
        loop_end = state.get("loop_start", 0.0) + state.get("loop_length", 0.0)
        if state.get("loop") and start_beat < loop_end <= end_beat:
            raise ValueError(
                "The Arrangement loop would wrap during capture; disable it or choose a range ending before the loop end"
            )

    check_loop(before)
    snapshot = conn.send_command("get_session_snapshot")
    if any(track.get("armed") for track in snapshot.get("tracks", [])):
        raise ValueError(
            "Disarm other tracks before bouncing; arrangement recording would also record those tracks"
        )
    tempo = before.get("tempo", 120.0)
    if not math.isfinite(tempo) or tempo <= 0:
        raise ValueError("Live returned an invalid tempo")
    duration = (end_beat - start_beat) * 60.0 / tempo
    if duration > MAX_RECORD_SECONDS:
        raise ValueError(
            f"Section is {duration:.0f}s; the recording cap is {MAX_RECORD_SECONDS:.0f}s"
        )
    creation_id = uuid.uuid4().hex
    owned_id = None
    recording_requested = playback_requested = False
    result = {"source": source, "cleanup_errors": []}
    failure = None

    def target():
        current = conn.send_command("get_session_snapshot")
        track = next((t for t in current.get("tracks", []) if t.get("id") == owned_id), None)
        if track is None:
            raise RuntimeError("The bounce track was removed; no other track will be edited")
        return {"track_index": track["index"], "expected_track_id": owned_id}

    def owned(command, **params):
        return conn.send_command(command, {**target(), **params})

    def transport(command, **params):
        return conn.send_command(command, {"expected_recording_track_id": owned_id, **params})

    try:
        created = conn.send_command("create_audio_track", {"index": -1}, operation_id=creation_id)
        owned_id = created.get("id")
        if not owned_id:
            raise RuntimeError("Bridge did not return a stable bounce-track identity")
        result.update({"track_index": created["index"], "track_id": owned_id})
        owned("set_track_name", name="Bounce")
        owned("set_track_routing", field="input_routing_type", display_name=source)
        owned("set_track_monitoring", state=2)
        owned("set_track_arm", arm=True)
        # Recheck after setup: a user may have armed a different track meanwhile.
        ready = conn.send_command("get_session_snapshot")
        if any(t.get("armed") and t.get("id") != owned_id for t in ready.get("tracks", [])):
            raise RuntimeError("Another track was armed during setup; recording was not started")
        ready_state = conn.send_command("get_session_info")
        if ready_state.get("is_playing") or ready_state.get("record_mode"):
            raise RuntimeError("Transport changed during setup; recording was not started")
        check_loop(ready_state)
        seek = transport("set_current_song_time", time=start_beat)
        if not seek.get("confirmed"):
            raise RuntimeError("Start position was not confirmed; recording was not started")
        recording_requested = True
        transport("set_record_mode", enabled=True)
        playback_requested = True
        started = transport("start_playback")
        if not started.get("confirmed"):
            raise RuntimeError("Playback was not confirmed")
        deadline = time.monotonic() + min(MAX_RECORD_SECONDS, duration * 2 + 5)
        while True:
            cancellable_wait(0.2)
            state = conn.send_command("get_session_info")
            if not state.get("is_playing") or not state.get("record_mode"):
                raise RuntimeError("Playback/recording changed during the bounce")
            check_loop(state)
            if state["current_song_time"] >= end_beat:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Recording exceeded its bounded capture time")
        transport("set_record_mode", enabled=False)
        recording_requested = False
        stopped = transport("stop_playback")
        if not stopped.get("confirmed"):
            raise RuntimeError("Recording stopped, but playback stop was not confirmed")
        playback_requested = False
        clips = owned("get_arrangement_clips").get("clips", [])
        recorded = [c for c in clips if c.get("is_audio_clip") and c.get("file_path")]
        result["file_path"] = recorded[-1]["file_path"] if recorded else None
        if not result["file_path"]:
            result["note"] = (
                "No recorded file was exposed by Live; inspect the retained audio files"
            )
    except Exception as exc:
        failure = exc
        raise
    finally:
        # Cancellation must not cancel the recovery commands themselves.
        with cleanup_scope():

            def recover(action):
                try:
                    return action()
                except Exception as exc:
                    result["cleanup_errors"].append(str(exc))
                    return None

            if owned_id is None:
                receipt = recover(
                    lambda: conn.send_command("get_operation_status", {"operation_id": creation_id})
                )
                owned_id = (((receipt or {}).get("response") or {}).get("result") or {}).get("id")
            # Always disarm the track we created, even when retaining it.
            if owned_id:
                recover(lambda: owned("set_track_arm", arm=False))
            current = recover(lambda: conn.send_command("get_session_info"))
            if recording_requested and (current is None or current.get("record_mode")):
                recover(lambda: transport("set_record_mode", enabled=False))
            if playback_requested and (current is None or current.get("is_playing")):
                stopped = recover(lambda: transport("stop_playback"))
                if stopped is not None and not stopped.get("confirmed"):
                    result["cleanup_errors"].append("Playback stop was not confirmed")
            settled = recover(lambda: conn.send_command("get_session_info"))
            result["transport_recovery"] = {
                "is_playing": settled.get("is_playing") if settled else None,
                "record_mode": settled.get("record_mode") if settled else None,
                "confirmed_stopped": bool(
                    settled and not settled.get("is_playing") and not settled.get("record_mode")
                ),
            }
            # Never rewind during recovery. Position alone cannot establish
            # ownership of a user's intervening seek, even inside the range.
            if cleanup and owned_id:
                removed = recover(lambda: owned("delete_track"))
                result["cleaned_up"] = removed is not None
            else:
                result["cleaned_up"] = False
            if failure is not None:
                failure.details = {**getattr(failure, "details", {}), "recording_recovery": result}
    return result
