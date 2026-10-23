"""Consumer tools exercised through the bridge's real command dispatcher."""

from types import SimpleNamespace as S

import pytest

from ableton_live_mcp.tools import devices, generators, keys, recipes


@pytest.fixture
def connection(bridge, bridge_connection, monkeypatch):
    bridge._song.create_midi_track = lambda index: bridge._song.tracks.append(S(name="Untitled"))
    bridge._search_browser = lambda *args: {
        "matches": [],
        "partial": True,
        "limit_reasons": ["scan_budget"],
    }
    for module in (devices, generators, keys):
        monkeypatch.setattr(module, "get_ableton_connection", lambda: bridge_connection)
    return bridge_connection


def test_empty_setup_sets_tempo_without_creating_tracks(connection, bridge):
    assert generators.setup_session(92.5, []) == {"tempo": 92.5, "tracks": []}
    assert bridge._song.tempo == 92.5 and bridge._song.tracks == []


def test_setup_checks_all_names_before_editing(connection, bridge):
    with pytest.raises(ValueError, match="string name"):
        generators.setup_session(90, [{"name": "good"}, {"name": None}])
    assert bridge.edits == [] and bridge._song.tempo == 120 and bridge._song.tracks == []


def test_incomplete_search_is_not_reported_as_no_match(connection, bridge):
    report = generators.setup_session(120, [{"name": "Bass", "instrument": "bass"}])
    track = report["tracks"][0]
    assert bridge._song.tracks[0].name == "Bass"
    assert track["search_partial"] is True
    assert "incomplete" in track["note"] and "no browser match" not in track["note"]
    loaded = recipes._try_load(connection, 0, "bass")
    assert loaded["instrument"] is None and loaded["search_partial"]
    assert "incomplete" in loaded["warning"]


def test_recipe_budget_is_checked_before_tempo_or_track_edits(connection, bridge):
    with pytest.raises(ValueError, match="4000"):
        recipes._build(
            connection,
            90,
            "C",
            "major",
            5000,
            genre="lofi",
            drum_style="lofi",
            bass_query="bass",
            chord_query="piano",
            chord_style="rootless",
        )
    assert bridge.edits == [] and bridge._song.tempo == 120 and bridge._song.tracks == []


def test_session_key_falls_back_on_legacy_bridge(connection, bridge):
    connection.supports_capability = lambda _: False
    bridge._get_session_snapshot = lambda: {"tracks": [{"index": 0, "type": "midi"}]}
    bridge._get_track_info = lambda _: {"clip_slots": [{"index": 0, "has_clip": True}]}
    bridge._get_clip_notes = lambda *args: {
        "notes": [{"pitch": pitch, "duration": 8, "velocity": 100} for pitch in (60, 64, 67)]
    }
    report = keys.detect_session_key()
    assert report["note_count"] == 3 and report["key"] == "C major"
    assert report["partial"] is False and report["read_mode"] == "legacy_per_clip"


def test_native_numeric_string_reaches_the_rack_parameter_on_old_bridge(connection, bridge):
    gain = S(
        name="Gain",
        min=0.0,
        max=1.0,
        value=0.2,
        is_enabled=True,
        is_quantized=False,
        str_for_value=str,
    )
    rack = S(can_have_chains=True, chains=[S(devices=[S(name="Utility", parameters=[gain])])])
    bridge._song.tracks = [S(devices=[rack])]
    connection.require_capability = lambda _: pytest.fail("native string needs no new capability")
    result = devices.set_chain_device_parameter(0, 0, 0, 0, "Gain", "0.5")
    assert result["value"] == gain.value == 0.5
