import copy

import pytest

from ableton_live_mcp.tools import audio


class FakeConnection:
    def __init__(self, failure=None):
        self.failure = failure
        self.failed = False
        self.commands = []
        self.state = {
            "tempo": 120.0,
            "is_playing": False,
            "record_mode": False,
            "current_song_time": 2.0,
        }
        self.tracks = [{"index": 0, "id": "session:original", "armed": False}]
        self.receipts = {}

    def require_capability(self, capability):
        pass

    def send_command(self, command, params=None, **kwargs):
        params = params or {}
        self.commands.append((command, dict(params)))
        result = {}
        if "expected_track_id" in params:
            assert self.tracks[params["track_index"]]["id"] == params["expected_track_id"]
        if command == "get_session_info":
            return dict(self.state)
        if command == "get_session_snapshot":
            return {"tracks": copy.deepcopy(self.tracks)}
        if command == "create_audio_track":
            self.tracks.append({"index": 1, "id": "session:bounce", "armed": False})
            result = {"index": 1, "id": "session:bounce"}
            self.receipts[kwargs["operation_id"]] = {
                "state": "completed",
                "response": {"result": result},
            }
        if command == "get_operation_status":
            return self.receipts.get(params["operation_id"], {"state": "unknown_or_expired"})
        if command == "set_track_arm":
            self.tracks[params["track_index"]]["armed"] = params["arm"]
        if command == "set_current_song_time":
            self.state["current_song_time"] = params["time"]
            result = {"confirmed": True}
        if command == "set_record_mode":
            self.state["record_mode"] = params["enabled"]
        if command == "start_playback":
            self.state["is_playing"] = True
            result = {"confirmed": True}
        if command == "stop_playback":
            self.state["is_playing"] = False
            result = {"confirmed": True}
        if command == "get_arrangement_clips":
            result = {"clips": [{"is_audio_clip": True, "file_path": "/tmp/bounce.wav"}]}
        if command == "delete_track":
            assert self.tracks[params["track_index"]]["id"] == "session:bounce"
            self.tracks.pop(params["track_index"])
        if command == self.failure and not self.failed:
            self.failed = True
            # Emulate a user reorder before recovery; the old index is unsafe.
            self.tracks.reverse()
            for i, track in enumerate(self.tracks):
                track["index"] = i
            raise RuntimeError("simulated lost acknowledgement")
        return result


@pytest.mark.parametrize(
    "failure",
    [
        "create_audio_track",
        "set_track_name",
        "set_track_routing",
        "set_track_monitoring",
        "set_track_arm",
        "set_current_song_time",
        "set_record_mode",
        "start_playback",
        "get_arrangement_clips",
    ],
)
def test_failure_recovers_only_owned_track(monkeypatch, failure):
    conn = FakeConnection(failure)
    monkeypatch.setattr(audio, "get_ableton_connection", lambda: conn)
    monkeypatch.setattr(
        audio, "cancellable_wait", lambda _: conn.state.update(current_song_time=8.0)
    )
    with pytest.raises(RuntimeError, match="simulated"):
        audio.record_section(4, 8)
    assert [track["id"] for track in conn.tracks] == ["session:original"]
    assert conn.state["record_mode"] is False
    assert conn.state["is_playing"] is False


def test_retained_track_is_disarmed_after_success(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(audio, "get_ableton_connection", lambda: conn)
    monkeypatch.setattr(
        audio, "cancellable_wait", lambda _: conn.state.update(current_song_time=8.0)
    )
    result = audio.record_section(4, 8, cleanup=False)
    assert result["file_path"] == "/tmp/bounce.wav"
    assert conn.tracks[1]["armed"] is False
    assert conn.state == {
        "tempo": 120.0,
        "is_playing": False,
        "record_mode": False,
        "current_song_time": 8.0,
    }


def test_refuses_other_armed_tracks_before_mutation(monkeypatch):
    conn = FakeConnection()
    conn.tracks[0]["armed"] = True
    monkeypatch.setattr(audio, "get_ableton_connection", lambda: conn)
    with pytest.raises(ValueError, match="Disarm"):
        audio.record_section(4, 8)
    assert all(command.startswith("get_") for command, _ in conn.commands)


@pytest.mark.parametrize("loop_start,loop_length,start,end", [(4, 4, 4, 8), (0, 4, 2, 6)])
def test_loop_wrap_is_rejected_before_edits(monkeypatch, loop_start, loop_length, start, end):
    conn = FakeConnection()
    conn.state.update(loop=True, loop_start=loop_start, loop_length=loop_length)
    monkeypatch.setattr(audio, "get_ableton_connection", lambda: conn)
    with pytest.raises(ValueError, match="loop would wrap"):
        audio.record_section(start, end)
    assert all(command.startswith("get_") for command, _ in conn.commands)


def test_lost_final_ack_stops_playback_beyond_capture_range(monkeypatch):
    class LostAck(FakeConnection):
        lost = False

        def send_command(self, command, params=None, **kwargs):
            result = super().send_command(command, params, **kwargs)
            if command == "set_record_mode" and params["enabled"] is False and not self.lost:
                self.lost = True
                self.state["current_song_time"] = 38.0
                raise RuntimeError("lost final acknowledgement")
            return result

    conn = LostAck()
    monkeypatch.setattr(audio, "get_ableton_connection", lambda: conn)
    monkeypatch.setattr(
        audio, "cancellable_wait", lambda _: conn.state.update(current_song_time=8.0)
    )
    with pytest.raises(RuntimeError, match="lost final") as error:
        audio.record_section(4, 8)
    assert conn.state["is_playing"] is False
    assert conn.state["record_mode"] is False
    assert error.value.details["recording_recovery"]["transport_recovery"]["confirmed_stopped"]


def test_unconfirmed_stop_is_retried_and_failure_is_reported(monkeypatch):
    class LateStop(FakeConnection):
        stops = 0
        always_fail = False

        def send_command(self, command, params=None, **kwargs):
            if command == "stop_playback":
                self.stops += 1
                if self.stops == 1 or self.always_fail:
                    self.state["current_song_time"] += 5
                    return {"confirmed": False}
            return super().send_command(command, params, **kwargs)

    for always_fail in (False, True):
        conn = LateStop()
        conn.always_fail = always_fail
        monkeypatch.setattr(audio, "get_ableton_connection", lambda: conn)
        monkeypatch.setattr(
            audio, "cancellable_wait", lambda _: conn.state.update(current_song_time=8.0)
        )
        with pytest.raises(RuntimeError, match="not confirmed") as error:
            audio.record_section(4, 8)
        assert conn.stops == 2
        recovery = error.value.details["recording_recovery"]
        assert recovery["transport_recovery"]["is_playing"] is always_fail
        if always_fail:
            assert "Playback stop was not confirmed" in recovery["cleanup_errors"]


@pytest.mark.parametrize("position", [6.0, 100.0])
def test_manual_seek_is_never_rewound_by_recovery(monkeypatch, position):
    from ableton_live_mcp.cancellation import ToolCancelled

    conn = FakeConnection()
    monkeypatch.setattr(audio, "get_ableton_connection", lambda: conn)

    def stop_and_seek(_):
        conn.state.update(current_song_time=position, is_playing=False, record_mode=False)
        raise ToolCancelled("user cancelled")

    monkeypatch.setattr(audio, "cancellable_wait", stop_and_seek)
    with pytest.raises(ToolCancelled):
        audio.record_section(4, 8, cleanup=False)
    assert conn.state["current_song_time"] == position
    assert conn.tracks[1]["armed"] is False
    assert [
        params["time"] for command, params in conn.commands if command == "set_current_song_time"
    ] == [4]
