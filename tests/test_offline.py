"""Unit tests for the offline .als parser (_als_parse) and its tools (offline).

Everything here is offline: no Live, no socket. The fixtures are small synthetic
gzipped .als files built inline so the assertions are exact — including the
fields that are easy to silently mis-read from Live's XML (mute is stored
inverted as the track activator; the master's dynamics usually sit inside a
rack; frozen tracks store phantom clips under FreezeSequencer).
"""

import gzip
import json

import pytest

from ableton_live_mcp.tools import offline
from ableton_live_mcp.tools._als_parse import _load

# ── synthetic fixture ────────────────────────────────────────────────

# Two tracks:
#  - "Keys": a MIDI track, muted (Speaker false), color 17, one 4-beat clip with
#    two notes, an Operator instrument, one automation lane, and a frozen clip
#    under FreezeSequencer that must NOT be counted.
#  - "Vox": an audio track, audible, with no clips (unfinished-work lint).
# Master: an Audio Effect Rack holding a Glue Compressor — dynamics inside a
# rack, which the no_master_dynamics lint must still see.
_ALS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12049" Creator="Ableton Live 12.0">
  <LiveSet>
    <Tracks>
      <MidiTrack>
        <Name><EffectiveName Value="Keys"/><UserName Value=""/></Name>
        <Color Value="17"/>
        <AutomationEnvelopes><Envelopes>
          <AutomationEnvelope/>
        </Envelopes></AutomationEnvelopes>
        <DeviceChain>
          <Mixer>
            <Speaker><Manual Value="false"/></Speaker>
            <SoloSink Value="false"/>
            <Volume><Manual Value="0.85"/></Volume>
            <Pan><Manual Value="-0.25"/></Pan>
          </Mixer>
          <MainSequencer>
            <ClipSlotList>
              <ClipSlot><ClipSlot><Value>
                <MidiClip>
                  <CurrentStart Value="0"/>
                  <CurrentEnd Value="4"/>
                  <Name Value="Chords"/>
                  <Loop><LoopOn Value="true"/></Loop>
                  <Notes>
                    <KeyTracks>
                      <KeyTrack>
                        <MidiKey Value="60"/>
                        <Notes>
                          <MidiNoteEvent Time="0" Duration="1" Velocity="100"/>
                          <MidiNoteEvent Time="2" Duration="1" Velocity="90"/>
                        </Notes>
                      </KeyTrack>
                    </KeyTracks>
                  </Notes>
                </MidiClip>
              </Value></ClipSlot></ClipSlot>
            </ClipSlotList>
          </MainSequencer>
          <FreezeSequencer>
            <ClipSlotList>
              <ClipSlot><ClipSlot><Value>
                <AudioClip>
                  <CurrentStart Value="0"/>
                  <CurrentEnd Value="4"/>
                  <Name Value="Freeze 1"/>
                </AudioClip>
              </Value></ClipSlot></ClipSlot>
            </ClipSlotList>
          </FreezeSequencer>
          <DeviceChain>
            <Devices>
              <Operator>
                <On><Manual Value="true"/></On>
                <UserName Value=""/>
              </Operator>
            </Devices>
          </DeviceChain>
        </DeviceChain>
      </MidiTrack>
      <AudioTrack>
        <Name><EffectiveName Value="Vox"/></Name>
        <Color Value="4"/>
        <DeviceChain>
          <Mixer>
            <Speaker><Manual Value="true"/></Speaker>
            <SoloSink Value="false"/>
            <Volume><Manual Value="0.6309573444801932"/></Volume>
            <Pan><Manual Value="0.0"/></Pan>
          </Mixer>
          <MainSequencer><ClipSlotList/></MainSequencer>
          <DeviceChain><Devices/></DeviceChain>
        </DeviceChain>
      </AudioTrack>
    </Tracks>
    <MainTrack>
      <Name><EffectiveName Value="Main"/></Name>
      <AutomationEnvelopes><Envelopes>
        <AutomationEnvelope/>
        <AutomationEnvelope/>
      </Envelopes></AutomationEnvelopes>
      <DeviceChain>
        <Mixer>
          <Speaker><Manual Value="true"/></Speaker>
          <Volume><Manual Value="1.0"/></Volume>
          <Pan><Manual Value="0.0"/></Pan>
          <Tempo><Manual Value="92.5"/></Tempo>
          <TimeSignature>
            <TimeSignatures>
              <RemoteableTimeSignature>
                <Numerator Value="4"/>
                <Denominator Value="4"/>
              </RemoteableTimeSignature>
            </TimeSignatures>
          </TimeSignature>
        </Mixer>
        <DeviceChain>
          <Devices>
            <AudioEffectGroupDevice>
              <On><Manual Value="true"/></On>
              <UserName Value="Master Bus"/>
              <Branches>
                <AudioEffectBranch>
                  <DeviceChain>
                    <AudioToAudioDeviceChain>
                      <Devices>
                        <GlueCompressor>
                          <On><Manual Value="true"/></On>
                          <UserName Value=""/>
                        </GlueCompressor>
                      </Devices>
                    </AudioToAudioDeviceChain>
                  </DeviceChain>
                </AudioEffectBranch>
              </Branches>
            </AudioEffectGroupDevice>
          </Devices>
        </DeviceChain>
      </DeviceChain>
    </MainTrack>
    <Scenes>
      <Scene><Name Value="Intro"/></Scene>
      <Scene><Name Value="Drop"/></Scene>
    </Scenes>
    <Locators>
      <Locators>
        <Locator><Time Value="16"/><Name Value="Verse"/></Locator>
      </Locators>
    </Locators>
  </LiveSet>
</Ableton>
"""


def _write_als(tmp_path, xml, name="Test Set.als"):
    p = tmp_path / name
    p.write_bytes(gzip.compress(xml.encode("utf-8")))
    return str(p)


@pytest.fixture
def als(tmp_path):
    return _write_als(tmp_path, _ALS_XML)


# ── parse layer ──────────────────────────────────────────────────────


def test_parse_basics(als):
    data, err = _load(als)
    assert err is None
    assert data["creator"] == "Ableton Live 12.0"
    assert data["tempo"] == 92.5
    assert len(data["tracks"]) == 2
    assert data["scenes"] == ["Intro", "Drop"]
    assert data["locators"] == [{"time": 16.0, "name": "Verse"}]


def test_track_mixer_fields(als):
    data, _ = _load(als)
    keys, vox = data["tracks"]
    # Mute is stored inverted as the track activator (Speaker false = muted).
    assert keys["muted"] is True
    assert vox["muted"] is False
    assert keys["soloed"] is False
    assert keys["color_index"] == 17
    assert keys["volume"] == 0.85
    assert keys["pan"] == -0.25
    assert keys["automation_lanes"] == 1
    assert data["master"]["automation_lanes"] == 2


def test_clips_skip_freeze_sequencer(als):
    data, _ = _load(als)
    keys = data["tracks"][0]
    # Only the real session clip counts; the frozen AudioClip must not.
    assert len(keys["clips"]) == 1
    clip = keys["clips"][0]
    assert clip["name"] == "Chords"
    assert clip["is_midi"] is True
    assert clip["length"] == 4.0
    assert clip["note_count"] == 2
    assert [n["pitch"] for n in clip["notes"]] == [60, 60]


def test_master_rack_devices_are_flattened(als):
    data, _ = _load(als)
    kinds = [(d["kind"], d.get("in_rack", False)) for d in data["master"]["devices"]]
    assert ("AudioEffectGroupDevice", False) in kinds
    assert ("GlueCompressor", True) in kinds


# ── error paths ──────────────────────────────────────────────────────


def test_missing_file_error(tmp_path):
    data, err = _load(str(tmp_path / "nope.als"))
    assert data is None
    assert "No file at" in err


def test_not_gzip_error(tmp_path):
    p = tmp_path / "fake.als"
    p.write_bytes(b"plain bytes, not gzip")
    data, err = _load(str(p))
    assert data is None
    assert "not a valid gzip" in err


def test_non_ableton_xml_error(tmp_path):
    p = tmp_path / "other.als"
    p.write_bytes(gzip.compress(b"<Score><Note/></Score>"))
    data, err = _load(str(p))
    assert data is None
    assert "not an Ableton" in err


# ── tools ────────────────────────────────────────────────────────────


def test_als_summary_tool(als):
    out = json.loads(offline.als_summary(None, als))
    assert out["tempo"] == 92.5
    assert out["track_count"] == 2
    tracks = {t["name"]: t for t in out["tracks"]}
    assert tracks["Keys"]["muted"] is True
    assert tracks["Vox"]["muted"] is False


def test_als_find_unfinished_tool(als):
    out = json.loads(offline.als_find_unfinished(None, als))
    codes = {i["code"] for i in out["issues"]}
    # The muted MIDI track and empty audio track must be flagged...
    assert "muted_track" in codes
    assert "empty_audio_track" in codes
    # ...but the Glue Compressor inside the master rack counts as dynamics.
    assert "no_master_dynamics" not in codes


def test_als_diff_tool(tmp_path):
    a = _write_als(tmp_path, _ALS_XML, "v1.als")
    b = _write_als(tmp_path, _ALS_XML.replace('Value="92.5"', 'Value="100"'), "v2.als")
    out = json.loads(offline.als_diff(None, a, b))
    assert out["changes"]["tempo"] == {"from": 92.5, "to": 100.0}
    assert out["changes"]["tracks_added"] == []
    assert out["changes"]["tracks_removed"] == []


def test_als_extract_midi_tool(als):
    out = json.loads(offline.als_extract_midi(None, als, 0))
    assert out["track"] == "Keys"
    assert len(out["clips"]) == 1
    assert len(out["clips"][0]["notes"]) == 2
    with pytest.raises(ValueError, match="No track at index"):
        offline.als_extract_midi(None, als, 9)
