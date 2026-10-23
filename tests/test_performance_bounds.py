import gzip

import pytest
from test_offline import _ALS_XML

from ableton_live_mcp.tools import _als_parse as parser
from ableton_live_mcp.tools import _als_xml
from ableton_live_mcp.tools.motif import additive, phase_pattern


def test_als_cache_invalidates_and_protects_data(tmp_path, monkeypatch):
    source = tmp_path / "song.als"
    source.write_bytes(gzip.compress(_ALS_XML.encode()))
    original = parser.load_gz_xml
    reads = []

    def counting(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(parser, "load_gz_xml", counting)
    first, error = parser._load(source)
    assert error is None
    first["tracks"][0]["name"] = "Corrupted by caller"
    second, _ = parser._load(source)
    assert second["tracks"][0]["name"] == "Keys" and len(reads) == 1
    source.write_bytes(gzip.compress(_ALS_XML.replace("Keys", "Bass").encode()))
    third, _ = parser._load(source)
    assert third["tracks"][0]["name"] == "Bass" and len(reads) == 2
    parser._load(source, refresh=True)
    assert len(reads) == 3


def test_summary_preserves_counts_without_returning_notes(tmp_path):
    source = tmp_path / "song.als"
    source.write_bytes(gzip.compress(_ALS_XML.encode()))
    data, error = parser._load(source, include_notes=False)
    assert error is None
    assert data["tracks"][0]["note_count"] == 2
    assert data["tracks"][0]["clips"][0]["notes"] == []


@pytest.mark.parametrize(
    "payload,limit,error",
    [
        (gzip.compress(b"<Ableton/>"), 10, None),
        (gzip.compress(b"<Ableton/>"), 9, "decompresses past"),
        (gzip.compress(b"<Ableton/>")[:-4], 1024, "not a valid gzip"),
        (gzip.compress(b"<Ableton>"), 1024, "Could not parse the XML"),
    ],
)
def test_xml_expansion_boundary_and_corruption(tmp_path, monkeypatch, payload, limit, error):
    source = tmp_path / "bounded.als"
    source.write_bytes(payload)
    monkeypatch.setattr(_als_xml, "MAX_XML_BYTES", limit)
    root, _, actual_error = _als_xml.load_gz_xml(source)
    if error:
        assert root is None and error in actual_error
    else:
        assert actual_error is None and root.tag == "Ableton"


def test_file_changed_during_parse_is_not_cached(tmp_path, monkeypatch):
    source = tmp_path / "changing.als"
    source.write_bytes(gzip.compress(_ALS_XML.encode()))
    original = parser.load_gz_xml

    def change_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_bytes(gzip.compress(_ALS_XML.replace("Keys", "Longer name").encode()))
        return result

    monkeypatch.setattr(parser, "load_gz_xml", change_after_read)
    data, error = parser._load(source)
    assert data is None and "changed" in error
    monkeypatch.setattr(parser, "load_gz_xml", original)
    data, error = parser._load(source)
    assert error is None and data["tracks"][0]["name"] == "Longer name"


def test_generator_limits_precede_allocation():
    from ableton_live_mcp.tools.generators import write_drum_grid

    note = {"pitch": 60, "start_time": 0, "duration": 1, "velocity": 100}
    with pytest.raises(ValueError, match="32000"):
        phase_pattern([note], 10**12, 0.5)
    with pytest.raises(ValueError, match="32000"):
        additive([note] * 10000, 10000)
    with pytest.raises(ValueError, match="finite"):
        phase_pattern([note], 8, float("nan"))
    for grid, error in [
        ("kick:" + "." * 1_048_576, "characters"),
        ("kick:" + "x" * 32001, "32000"),
        ("kick: x?", "patterns"),
    ]:
        with pytest.raises(ValueError, match=error):
            write_drum_grid(0, 0, grid)


def test_full_note_read_stops_before_building_an_oversized_reply(bridge):
    from types import SimpleNamespace as S

    visited = []

    def notes():
        for _ in range(40000):
            visited.append(1)
            yield S(pitch=60, start_time=0, duration=1, velocity=100, mute=False)

    clip = S(is_midi_clip=True, get_all_notes_extended=notes)
    bridge._song.tracks = [S(clip_slots=[S(has_clip=True, clip=clip)])]
    with pytest.raises(ValueError, match="32000"):
        bridge._get_clip_notes(0, 0)
    assert len(visited) <= 32002
