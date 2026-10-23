from ableton_live_mcp.tools.analysis import _snapshot_delta


def snapshot(tracks, scope="one"):
    return {"snapshot_scope": scope, "tracks": tracks}


def test_rename_does_not_report_deletion_or_addition():
    result = _snapshot_delta(
        snapshot([{"id": 1, "name": "Bass"}]), snapshot([{"id": 1, "name": "Sub"}])
    )
    assert result["tracks_added"] == result["tracks_removed"] == []
    assert result["tracks_renamed"] == [{"id": 1, "from": "Bass", "to": "Sub"}]


def test_duplicate_names_reorder_and_real_replacement_are_distinct():
    before = snapshot(
        [
            {"id": 1, "name": "Track", "index": 0, "volume": 0.5},
            {"id": 2, "name": "Track", "index": 1},
        ]
    )
    after = snapshot(
        [
            {"id": 2, "name": "Track", "index": 0},
            {"id": 1, "name": "Track", "index": 1, "volume": 0.6},
        ]
    )
    result = _snapshot_delta(before, after)
    assert result["tracks_added"] == result["tracks_removed"] == result["tracks_renamed"] == []
    assert len(result["tracks_modified"]) == 2
    assert result["tracks_modified"][1]["volume"] == {"from": 0.5, "to": 0.6}
    result = _snapshot_delta(
        snapshot([{"id": 1, "name": "Bass"}]), snapshot([{"id": 3, "name": "Bass"}])
    )
    assert result["tracks_added"] == result["tracks_removed"] == ["Bass"]
    assert result["tracks_renamed"] == []


def test_legacy_or_reset_identities_are_not_guessed():
    for before, after in [
        ({"tracks": [{"name": "Old"}]}, {"tracks": [{"name": "New"}]}),
        (snapshot([{"id": 1, "name": "Old"}]), snapshot([{"id": 1, "name": "New"}], "two")),
    ]:
        result = _snapshot_delta(before, after)
        assert result["track_identity_available"] is False
        assert "tracks_removed" not in result


def test_bridge_ids_survive_rename_and_reorder_and_do_not_recycle(bridge):
    identify = bridge._snapshot_track_ids

    class Track:
        name = "Same"

    a, b, c = Track(), Track(), Track()
    first, second = identify([a, b])
    assert first != second
    a.name = "Renamed"
    assert identify([b, a]) == [second, first]
    retained, replacement = identify([b, c])
    assert retained == second and replacement not in (first, second)
    retained, returned = identify([b, a])
    assert retained == second and returned not in (first, second, replacement)
