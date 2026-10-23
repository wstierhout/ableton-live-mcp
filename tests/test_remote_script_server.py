import sys
from types import SimpleNamespace


def test_idle_socket_timeout_is_not_logged_as_an_accept_error(bridge_module, monkeypatch):
    class LegacySocketTimeout(OSError):
        """Model Live 11 where socket.timeout is not caught by TimeoutError."""

    class DifferentTimeoutError(OSError):
        pass

    class IdleServer:
        def __init__(self, owner):
            self.owner = owner
            self.accept_count = 0

        def settimeout(self, value):
            assert value == 1.0

        def accept(self):
            self.accept_count += 1
            if self.accept_count == 2:
                self.owner.running = False
            raise LegacySocketTimeout("timed out")

    sleep_calls = []
    monkeypatch.setattr(bridge_module, "socket", SimpleNamespace(timeout=LegacySocketTimeout))
    monkeypatch.setattr(bridge_module, "TimeoutError", DifferentTimeoutError, raising=False)
    monkeypatch.setattr(bridge_module, "time", SimpleNamespace(sleep=sleep_calls.append))

    bridge = SimpleNamespace(
        running=True,
        client_sockets=[],
        client_threads=[],
        log_messages=[],
        show_message=lambda _message: None,
    )
    bridge.log_message = bridge.log_messages.append
    bridge.server = IdleServer(bridge)

    bridge_module.AbletonMCP._server_thread(bridge)

    assert bridge.log_messages == ["Server thread started", "Server thread stopped"]
    # The loop must keep accepting after an idle timeout ...
    assert bridge.server.accept_count == 2
    # ... without falling into the error-path backoff sleep.
    assert sleep_calls == []


def test_device_categories_use_live_enum_not_class_names(bridge, monkeypatch):
    types = SimpleNamespace(instrument=object(), audio_effect=object(), midi_effect=object())
    monkeypatch.setitem(
        sys.modules, "Live", SimpleNamespace(Device=SimpleNamespace(DeviceType=types))
    )
    for name, value, drum, rack, expected in [
        ("Wavetable", types.instrument, False, False, "instrument"),
        ("Limiter", types.audio_effect, False, False, "audio_effect"),
        ("MidiChord", types.midi_effect, False, False, "midi_effect"),
        ("InstrumentGroupDevice", types.instrument, False, True, "rack"),
        ("DrumGroupDevice", types.instrument, True, True, "drum_machine"),
        ("FutureDevice", object(), False, False, "unknown"),
    ]:
        device = SimpleNamespace(
            class_name=name,
            class_display_name=name,
            type=value,
            can_have_drum_pads=drum,
            can_have_chains=rack,
        )
        assert bridge._get_device_type(device) == expected


def test_clip_lookup_errors_preserve_contents_and_operation_receipt(bridge):
    clip = SimpleNamespace(name="Original")
    bridge._song.tracks = [SimpleNamespace(clip_slots=[SimpleNamespace(has_clip=True, clip=clip)])]
    for track_index, clip_index in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        result = bridge.dispatch(
            {
                "type": "set_clip_name",
                "params": {"track_index": track_index, "clip_index": clip_index, "name": "Wrong"},
            }
        )
        assert result["status"] == "error"
        assert "index out of range" in result["message"]
        assert bridge._receipts[result["operation_id"]]["state"] == "failed"
        assert clip.name == "Original"
