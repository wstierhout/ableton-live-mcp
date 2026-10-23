"""Exercise real bridge dispatch against a simulated, delayed Live main thread."""

import sys
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture(params=["extended", "legacy"])
def midi_clip(request, bridge, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "Live",
        SimpleNamespace(Clip=SimpleNamespace(MidiNoteSpecification=SimpleNamespace)),
    )
    old_note = SimpleNamespace(pitch=50, start_time=0.0, duration=1.0, velocity=90, mute=False)
    clip = SimpleNamespace(
        name="Phrase", length=4.0, is_midi_clip=True, notes=[old_note], writes=[]
    )

    def clear(*args):
        clip.writes.append("clear")
        clip.notes.clear()

    if request.param == "extended":
        clip.remove_notes_extended = clear
        clip.add_new_notes = lambda notes: clip.notes.extend(notes)
        clip.get_all_notes_extended = lambda: clip.notes
        clip.get_notes_extended = lambda *args: clip.notes
    else:
        fields = ("pitch", "start_time", "duration", "velocity", "mute")
        clip.remove_notes = clear
        clip.set_notes = lambda notes: clip.notes.extend(
            SimpleNamespace(**dict(zip(fields, n))) for n in notes
        )
        clip.get_notes = lambda *args: [tuple(getattr(n, k) for k in fields) for n in clip.notes]
    bridge._song.tracks = [SimpleNamespace(clip_slots=[SimpleNamespace(has_clip=True, clip=clip)])]
    return clip


def test_batch_changes_volume_and_replaces_notes(bridge, bridge_connection, midi_clip):
    volume = SimpleNamespace(value=0.2, min=0.0, max=1.0)
    bridge._song.tracks[0].mixer_device = SimpleNamespace(volume=volume)
    note = {"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100}
    bridge_connection.send_command(
        "batch",
        {
            "commands": [
                {"type": "set_track_volume", "params": {"track_index": 0, "volume": 0.7}},
                {
                    "type": "add_notes_to_clip",
                    "params": {"track_index": 0, "clip_index": 0, "notes": [note]},
                },
            ]
        },
    )
    observed = bridge_connection.send_command("get_clip_notes", {"track_index": 0, "clip_index": 0})
    assert volume.value == 0.7
    assert [{key: n[key] for key in note} for n in observed["notes"]] == [note]


@pytest.mark.parametrize(
    "command, payload",
    [
        ("add_notes_to_clip", {"notes": [{"pitch": "bad"}]}),
        (
            "edit_notes",
            {"remove": [{"pitch": 50, "start_time": 0}], "add": [{"duration": "bad"}]},
        ),
        ("edit_notes", {"remove": [{"pitch": 50, "start_time": 0}, {"pitch": 60}]}),
    ],
)
def test_invalid_note_edit_preserves_old_contents(bridge, midi_clip, command, payload):
    original = list(midi_clip.notes)
    reply = bridge.dispatch(
        {"type": command, "params": {"track_index": 0, "clip_index": 0, **payload}}
    )
    assert reply["status"] == "error", reply
    assert midi_clip.notes == original
    assert midi_clip.writes == []


def test_automation_prepares_every_point_before_clear(bridge):
    original = [(0, 0.5)]
    envelope = list(original)
    writes = []
    param = SimpleNamespace(name="Gain", min=0, max=1)
    clip = SimpleNamespace(
        clear_envelope=lambda p: (writes.append("clear"), envelope.clear()),
        automation_envelope=lambda p: SimpleNamespace(
            insert_step=lambda t, duration, v: envelope.append((t, v))
        ),
    )
    bridge._get_clip = lambda *args: clip
    bridge._get_device = lambda *args: SimpleNamespace(name="Utility", parameters=[param])
    params = {"track_index": 0, "clip_index": 0, "device_index": 0, "parameter": "Gain"}
    for invalid in ({"time": "bad", "value": 0.8}, {"time": 1, "value": 2}):
        response = bridge.dispatch(
            {
                "type": "write_automation",
                "params": {**params, "points": [{"time": 0, "value": 0.2}, invalid]},
            }
        )
        assert response["status"] == "error", response
        assert envelope == original and writes == []
    response = bridge.dispatch(
        {
            "type": "write_automation",
            "params": {**params, "points": [{"time": 0, "value": 0.2}, {"time": 1, "value": 0.8}]},
        }
    )
    assert response["status"] == "success", response
    assert envelope == [(0, 0.2), (1, 0.8)]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "load_instrument_or_effect", "params": {"track_index": 0}},
        {"type": "set_tempo", "params": {"tempo": "120"}},
        {"type": "set_tempo", "params": {"tempo": 0}},
        {"type": "set_track_name", "params": {"track_index": -1, "name": "bad"}},
        {"type": "set_track_name", "params": {"track_index": 0, "name": "ok", "typo": 1}},
        {"type": "create_locator", "params": {"time": 16}},
        {"type": "batch_commands", "params": {"commands": []}},
        {"type": "save_set"},
        {"type": "record_section"},
        {
            "type": "add_notes_to_clip",
            "params": {"track_index": 0, "clip_index": 0, "notes": [{"probability": 2}]},
        },
        {
            "type": "clip_operation",
            "params": {
                "track_index": 0,
                "clip_index": 0,
                "op": "duplicate_loop",
                "params": {"typo_key": 5},
            },
        },
        {
            "type": "clip_operation",
            "params": {"track_index": 0, "clip_index": 0, "op": "duplicate_region"},
        },
    ],
)
def test_batch_preflight_makes_no_edits(bridge, bad):
    response = bridge.dispatch(
        {"type": "batch", "params": {"commands": [{"type": "create_midi_track"}, bad]}}
    )
    assert response["status"] == "error"
    assert response["details"]["failed_step"] == 2
    assert response["details"]["completed_count"] == 0
    assert bridge.edits == []


def test_batch_aliases_shapes_and_create_then_target(bridge):
    bridge._create_midi_track = lambda index: bridge.edits.append("create") or {"index": 0}
    bridge._load_browser_item = lambda index, uri: bridge.edits.append((index, uri)) or {}
    bridge._clip_op = lambda *args: bridge.edits.append(args) or {}
    commands = [
        {"type": "create_midi_track"},
        {"type": "load_instrument_or_effect", "params": {"track_index": 0, "uri": "device:x"}},
        {"type": "load_browser_item", "params": {"track_index": 0, "item_uri": "device:y"}},
        {
            "type": "clip_operation",
            "params": {
                "track_index": 0,
                "clip_index": 0,
                "op": "duplicate_region",
                "region_start": 0,
                "region_length": 4,
                "destination_time": 8,
            },
        },
    ]
    response = bridge.dispatch({"type": "batch", "params": {"commands": commands}})
    assert response["status"] == "success", response
    assert bridge.edits == [
        "begin",
        "create",
        (0, "device:x"),
        (0, "device:y"),
        (0, 0, "duplicate_region", {"region_start": 0, "region_length": 4, "destination_time": 8}),
        "end",
    ]


def test_batch_conflict_and_runtime_receipt(bridge, bridge_module):
    with pytest.raises(ValueError, match="Conflicting"):
        bridge._normalize_batch_command(
            {
                "type": "load_instrument_or_effect",
                "params": {"track_index": 0, "uri": "x", "item_uri": "y"},
            }
        )
    bridge._create_midi_track = lambda index: bridge.edits.append("create") or {"index": 0}
    bridge._load_browser_item = lambda *args: (_ for _ in ()).throw(
        bridge_module.CommandFailure("Missing device", {"may_have_applied": False})
    )
    command = {
        "type": "batch",
        "operation_id": "retry-this",
        "params": {
            "commands": [
                {"type": "create_midi_track"},
                {
                    "type": "load_instrument_or_effect",
                    "params": {"track_index": 0, "uri": "missing"},
                },
            ]
        },
    }
    response = bridge.dispatch(command)
    details = response["details"]
    assert details["failed_index"] == 1 and details["failed_step"] == 2
    assert details["completed_count"] == 1
    assert details["may_have_applied"] is True
    assert details["completed_results"][0]["result"] == {"index": 0}
    assert bridge.dispatch(command) == response
    assert bridge.edits.count("create") == 1
    command["params"]["commands"] = [{"type": "create_midi_track"}]
    assert "another command" in bridge.dispatch(command)["message"]


def test_timeout_before_start_never_applies_late(bridge):
    bridge._COMMAND_TIMEOUTS = {"create_midi_track": 0.01}
    bridge._create_midi_track = lambda index: bridge.edits.append("create")
    replies = []
    thread = threading.Thread(
        target=lambda: replies.append(bridge._process_command({"type": "create_midi_track"}))
    )
    thread.start()
    thread.join(1)
    assert replies[0]["details"]["state"] == "cancelled_before_start"
    bridge.pending.pop(0)()
    assert bridge.edits == []


def test_timeout_after_start_reports_uncertain_then_receipt_completes(bridge):
    bridge._COMMAND_TIMEOUTS = {"create_midi_track": 0.01}

    def slow(index):
        time.sleep(0.04)
        return {"index": 0}

    bridge._create_midi_track = slow
    response = bridge.dispatch({"type": "create_midi_track", "operation_id": "slow"})
    assert response["details"]["may_have_applied"] is True
    assert bridge._get_operation_status("slow")["state"] == "completed"


def test_transport_and_locator_wait_for_later_ticks(bridge):
    song = bridge._song
    song.start_playing = lambda: bridge.schedule_message(
        1, lambda: setattr(song, "is_playing", True)
    )
    response = bridge.dispatch({"type": "start_playback"})
    assert response["result"]["confirmed"] is True
    assert response["result"]["playing"] is True
    song.is_playing = False
    song.cue_points = []

    def create_cue():
        bridge.schedule_message(
            1,
            lambda: song.cue_points.append(
                SimpleNamespace(time=song.current_song_time, name="Untitled")
            ),
        )

    song.set_or_delete_cue = create_cue
    response = bridge.dispatch({"type": "create_locator", "params": {"time": 16, "name": "Chorus"}})
    assert response["result"]["confirmed"] is True
    assert song.cue_points[0].time == 16 and song.cue_points[0].name == "Chorus"
    bridge.dispatch({"type": "create_locator", "params": {"time": 16, "name": "Hook"}})
    assert len(song.cue_points) == 1 and song.cue_points[0].name == "Hook"


def test_stale_track_guard_including_bridge_restart(bridge):
    first, second = object(), object()
    bridge._song.tracks = [first, second]
    ids = bridge._snapshot_track_ids(bridge._song.tracks)
    bridge._song.tracks.reverse()
    with pytest.raises(ValueError, match="Stale"):
        bridge._guard_target({"track_index": 0, "expected_track_id": ids[0]})
    assert bridge._guard_target({"track_index": 1, "expected_track_id": ids[0]}) == {
        "track_index": 1
    }
    del bridge._snapshot_scope
    with pytest.raises(ValueError, match="Stale"):
        bridge._guard_target({"track_index": 1, "expected_track_id": ids[0]})


def test_browser_scan_is_bounded_and_reports_partial(bridge):
    leaf = SimpleNamespace(name="Piano", uri="x", is_loadable=True, is_folder=False, is_device=True)
    root = SimpleNamespace(children=[leaf] * 60000)
    bridge.application = lambda: SimpleNamespace(browser=SimpleNamespace(instruments=root))
    result = bridge.dispatch(
        {
            "type": "search_browser",
            "params": {"query": "no match", "category": "instruments", "max_results": 10},
        },
    )["result"]
    assert result["visited"] <= bridge._BROWSER_NODE_LIMIT
    assert result["partial"] is True


def test_seek_does_not_confirm_old_state_and_eventually_times_out(bridge):
    song = bridge._song

    class DelayedSong:
        is_playing = False
        tempo = 120
        current = 2.0

        @property
        def current_song_time(self):
            return self.current

        @current_song_time.setter
        def current_song_time(self, value):
            bridge.schedule_message(
                1, lambda: bridge.schedule_message(1, lambda: setattr(self, "current", value))
            )

    bridge._song = DelayedSong()
    response = bridge.dispatch({"type": "set_current_song_time", "params": {"time": 16}})
    assert response["result"]["confirmed"] is True
    assert response["result"]["observed"]["current_song_time"] == 16
    results = []
    bridge._later_readback(
        lambda: {"playing": False}, lambda value: value["playing"], {"playing": True}, timeout=0
    ).start(lambda result=None, error=None: results.append(result))
    bridge.pending.pop(0)()
    assert results[0]["confirmed"] is False
    assert results[0]["status"] == "requested_unconfirmed"
    bridge._song = song


def test_rack_parameter_matches_top_level_conversion(bridge):
    class Parameter:
        name = "Gain"
        min, max = 0.0, 1.0
        value = 0.5
        is_enabled = True
        is_quantized = False

        def str_for_value(self, value):
            return f"{value * 24 - 12:.3f} dB"

    parameter = Parameter()
    device = SimpleNamespace(name="Utility", parameters=[parameter])
    rack = SimpleNamespace(
        name="Rack", can_have_chains=True, chains=[SimpleNamespace(devices=[device])]
    )
    bridge._get_device = lambda *args: rack
    result = bridge._set_chain_device_parameter(0, 0, 0, 0, "Gain", "2 dB")
    assert result["requested"] == "2 dB"
    assert float(result["display"].split()[0]) == pytest.approx(2.0, abs=0.01)
    result = bridge._set_chain_device_parameter(0, 0, 0, 0, "Gain", 2.0)
    assert result["native"] == 1.0 and result["warning"]
    parameter.is_enabled = False
    with pytest.raises(ValueError, match="disabled"):
        bridge._set_chain_device_parameter(0, 0, 0, 0, "Gain", 0.0)
    assert parameter.value == 1.0


def test_bulk_notes_reports_partial_coverage(bridge):
    note = SimpleNamespace(pitch=60, start_time=0.0, duration=1.0, velocity=100, mute=False)
    clip = SimpleNamespace(is_midi_clip=True, get_all_notes_extended=lambda: [note] * 32005)
    bridge._song.tracks = [
        SimpleNamespace(has_midi_input=True, clip_slots=[SimpleNamespace(has_clip=True, clip=clip)])
    ]
    result = bridge.dispatch({"type": "get_session_notes"})["result"]
    assert len(result["notes"]) == 32000
    assert result["partial"] is True
    assert result["tracks_analyzed"] == 1


def test_recording_guard_preserves_another_tracks_recording(bridge):
    original = SimpleNamespace(can_be_armed=True, arm=False)
    owned = SimpleNamespace(can_be_armed=True, arm=True)
    bridge._song.tracks = [original, owned]
    owner = bridge._snapshot_track_ids(bridge._song.tracks)[1]
    assert bridge._guard_target({"expected_recording_track_id": owner, "enabled": False}) == {
        "enabled": False
    }
    original.arm = True
    with pytest.raises(ValueError, match="ownership changed"):
        bridge._guard_target({"expected_recording_track_id": owner, "enabled": False})
    bridge._song.tracks = [original]
    with pytest.raises(ValueError, match="ownership changed"):
        bridge._guard_target({"expected_recording_track_id": owner, "enabled": False})


def test_browser_reaches_later_roots_and_yields(bridge):
    leaf = SimpleNamespace(
        name="Other", uri="other", is_loadable=True, is_folder=False, is_device=True
    )
    reverb = SimpleNamespace(
        name="Reverb", uri="reverb", is_loadable=True, is_folder=False, is_device=True
    )
    bridge.application = lambda: SimpleNamespace(
        browser=SimpleNamespace(
            sounds=SimpleNamespace(children=[leaf] * 2600),
            audio_effects=SimpleNamespace(children=[reverb]),
        )
    )
    callbacks = []

    def schedule(delay, callback):
        callbacks.append(delay)
        bridge.pending.append(callback)

    bridge.schedule_message = schedule
    result = bridge.dispatch(
        {"type": "search_browser", "params": {"query": "reverb", "max_results": 5}}
    )["result"]
    assert result["matches"] == [{"name": "Reverb", "uri": "reverb", "is_device": True}]
    assert result["partial"] is False
    assert len(callbacks) > 2


@pytest.mark.parametrize("requested,expected", [(200, 200), (2000, 1000), (0, 0)])
def test_browser_legacy_queries_have_bounded_results(bridge, requested, expected):
    leaf = SimpleNamespace(name="Item", uri="x", is_loadable=True, is_folder=False, is_device=True)
    bridge.application = lambda: SimpleNamespace(
        browser=SimpleNamespace(instruments=SimpleNamespace(children=[leaf] * 1100))
    )
    result = bridge.dispatch(
        {"type": "search_browser", "params": {"query": "", "max_results": requested}}
    )["result"]
    assert len(result["matches"]) == expected
    assert result["requested_max_results"] == requested
    assert result["partial"] is True


def test_slow_bulk_reads_continue_on_later_ticks(bridge, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    note = {"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100}
    clip = SimpleNamespace(is_midi_clip=True)
    bridge._song.tracks = [
        SimpleNamespace(
            has_midi_input=True,
            clip_slots=[SimpleNamespace(has_clip=True, clip=clip) for _ in range(50)],
        )
    ]

    def read_notes(clip, max_notes=None):
        clock[0] += 0.004
        return [dict(note)]

    bridge._read_all_notes = read_notes
    response = bridge.dispatch({"type": "get_session_notes"})
    assert response["result"]["clips_analyzed"] == 50
    assert response["result"]["partial"] is False
    assert clock[0] > 0.05  # The former entire-scan budget must not truncate this.


def test_read_polling_does_not_evict_mutation_receipts(bridge):
    bridge._create_midi_track = lambda index: bridge.edits.append("create") or {"index": 0}
    bridge._get_session_info = lambda: {"tempo": 120}
    command = {"type": "create_midi_track", "operation_id": "edit-X"}
    original = bridge.dispatch(command)
    for index in range(300):
        bridge.dispatch({"type": "get_session_info", "operation_id": f"poll-{index}"})
    assert len(bridge._receipts) == 1
    assert bridge.dispatch(command) == original
    assert bridge.edits == ["create"]


def test_cancelled_queued_attempt_can_retry_without_reviving_old_callback(bridge):
    bridge._COMMAND_TIMEOUTS = {"create_midi_track": 0.01}
    bridge._create_midi_track = lambda index: bridge.edits.append("create") or {}
    command = {"type": "create_midi_track", "operation_id": "retry-cancelled"}
    replies = []
    thread = threading.Thread(target=lambda: replies.append(bridge._process_command(command)))
    thread.start()
    thread.join(1)
    assert replies[0]["details"]["state"] == "cancelled_before_start"
    bridge._COMMAND_TIMEOUTS = {"create_midi_track": 1.0}
    assert bridge.dispatch(command)["status"] == "success"
    assert bridge.edits == ["create"]


def test_large_receipts_do_not_serialize_on_main_thread(bridge, monkeypatch):
    import json

    original = json.dumps

    def checked(*args, **kwargs):
        assert threading.current_thread() is not bridge._main_thread
        return original(*args, **kwargs)

    monkeypatch.setattr(json, "dumps", checked)
    bridge._create_midi_track = lambda index: {"notes": [{}] * 100000}
    result = bridge.dispatch({"type": "create_midi_track", "operation_id": "large"})
    assert result["status"] == "success"
    receipt = bridge._get_operation_status("large")
    assert receipt["response"]["details"] == {"state": "completed", "result_retained": False}


def test_large_failed_receipt_does_not_claim_success(bridge):
    failure = bridge._process_command.__globals__["CommandFailure"]

    def fail(index):
        raise failure("Edit failed", {"data": "x" * 70000})

    bridge._create_midi_track = fail
    bridge.dispatch({"type": "create_midi_track", "operation_id": "failed"})
    receipt = bridge._get_operation_status("failed")["response"]
    assert "Operation failed" in receipt["message"]
    assert receipt["details"]["state"] == "failed"


def test_bridge_works_without_crypto_module(bridge, monkeypatch):
    namespace = bridge._process_command.__globals__
    monkeypatch.setitem(namespace, "_sha256", None)
    bridge._create_midi_track = lambda index: bridge.edits.append("create") or {}
    command = {"type": "create_midi_track", "operation_id": "no-crypto"}
    first = bridge.dispatch(command)
    assert first["status"] == "success"
    assert bridge.dispatch(command) == first
    assert bridge.edits == ["create"]
    huge = {"type": "create_midi_track", "operation_id": "opaque", "params": {"extra": "x" * 40000}}
    assert bridge.dispatch(huge)["status"] == "success"
    assert bridge.dispatch(huge)["status"] == "error"
    assert bridge.edits == ["create", "create"]


def test_chain_readback_failure_preserves_applied_success_and_numeric_strings(bridge):
    class Parameter:
        min, max, value = 0.0, 1.0, 0.0
        name, is_enabled, is_quantized = "Gain", True, False

        def str_for_value(self, value):
            raise RuntimeError("display unavailable")

    parameter = Parameter()
    device = SimpleNamespace(name="Utility", parameters=[parameter])
    rack = SimpleNamespace(
        name="Rack", can_have_chains=True, chains=[SimpleNamespace(devices=[device])]
    )
    bridge._get_device = lambda *args: rack
    result = bridge._set_chain_device_parameter(0, 0, 0, 0, "Gain", "0.5")
    assert result["native"] == parameter.value == 0.5
    assert result["requested"] == "0.5"
    assert result["display"] is None
    assert "Value applied" in result["warning"]


def test_identity_lookup_scales_linearly_for_hashable_proxy_replacements(bridge):
    comparisons = [0]

    class Track:
        def __init__(self, key):
            self.key = key

        def __hash__(self):
            return self.key

        def __eq__(self, other):
            comparisons[0] += 1
            return self.key == other.key

    original = bridge._snapshot_track_ids([Track(key) for key in range(1000)])
    assert bridge._snapshot_track_ids([Track(key) for key in range(999, -1, -1)]) == original[::-1]
    assert comparisons[0] <= 2000


def test_unhashable_replacement_proxies_keep_their_identity(bridge):
    class Track:
        __hash__ = None

        def __init__(self, key):
            self.key = key

        def __eq__(self, other):
            return self.key == other.key

    first = bridge._snapshot_track_ids([Track(1), Track(2)])
    assert bridge._snapshot_track_ids([Track(2), Track(1)]) == first[::-1]
