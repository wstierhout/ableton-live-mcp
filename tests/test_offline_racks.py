"""Unit tests for the offline .adg/.adv rack parser in offline_racks.

Everything here is offline: no Live, no socket. The core cases run against small
synthetic gzipped .adg fixtures built inline so the assertions are exact; one
extra test parses a real factory drum-rack .adg if the pack is installed (and
skips cleanly if it is not).
"""

import glob
import gzip
import json

import pytest

from ableton_live_mcp.tools import offline_racks as r
from ableton_live_mcp.tools.offline_racks import (
    adg_analyze,
    adg_edition,
    adg_summary,
)

# ── synthetic fixtures ───────────────────────────────────────────────

# An instrument rack: one chain "Lead" with a Suite-only Operator and a
# Standard Reverb, one named macro ("Cutoff") and one default-named macro that
# must be filtered out.
_INSTRUMENT_RACK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12049" Creator="Ableton Live 12.0" Revision="abc">
  <GroupDevicePreset>
    <OverwriteProtectionNumber Value="1"/>
    <Device>
      <InstrumentGroupDevice>
        <UserName Value="Synth Rack"/>
        <MacroControls.0><Manual Value="64"/></MacroControls.0>
        <MacroDisplayNames.0 Value="Cutoff"/>
        <MacroControls.1><Manual Value="0"/></MacroControls.1>
        <MacroDisplayNames.1 Value="Macro 2"/>
      </InstrumentGroupDevice>
    </Device>
    <BranchPresets>
      <InstrumentBranchPreset>
        <Name Value="Lead"/>
        <IsSoloed Value="false"/>
        <DevicePresets>
          <AbletonDevicePreset>
            <Device>
              <Operator>
                <On><Manual Value="true"/></On>
              </Operator>
            </Device>
          </AbletonDevicePreset>
          <AbletonDevicePreset>
            <Device>
              <Reverb>
                <On><Manual Value="false"/></On>
              </Reverb>
            </Device>
          </AbletonDevicePreset>
        </DevicePresets>
      </InstrumentBranchPreset>
    </BranchPresets>
  </GroupDevicePreset>
</Ableton>
"""

# A drum rack: two pads. ReceivingNote is stored inverted (128 - note): 92 -> 36
# (C1), 90 -> 38 (D1). Pad 1 carries a Simpler + sample; pad 2 a Suite Sampler.
_DRUM_RACK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12049" Creator="Ableton Live 12.0" Revision="abc">
  <GroupDevicePreset>
    <Device>
      <DrumGroupDevice>
        <UserName Value="Test Kit"/>
        <MacroControls.0><Manual Value="100"/></MacroControls.0>
        <MacroDisplayNames.0 Value="Drive"/>
      </DrumGroupDevice>
    </Device>
    <BranchPresets>
      <DrumBranchPreset>
        <Name Value="Kick Pad"/>
        <IsSoloed Value="false"/>
        <ZoneSettings>
          <ReceivingNote Value="92"/>
          <SendingNote Value="60"/>
          <ChokeGroup Value="0"/>
        </ZoneSettings>
        <DevicePresets>
          <AbletonDevicePreset>
            <Device>
              <OriginalSimpler>
                <On><Manual Value="true"/></On>
                <Player><SampleRef><FileRef>
                  <Name Value="Kick.wav"/>
                  <RelativePath Value="Samples/Kick.wav"/>
                  <Path Value="/abs/Samples/Kick.wav"/>
                </FileRef></SampleRef></Player>
              </OriginalSimpler>
            </Device>
          </AbletonDevicePreset>
        </DevicePresets>
      </DrumBranchPreset>
      <DrumBranchPreset>
        <Name Value="Snare Pad"/>
        <ZoneSettings>
          <ReceivingNote Value="90"/>
        </ZoneSettings>
        <DevicePresets>
          <AbletonDevicePreset>
            <Device>
              <Sampler><On><Manual Value="true"/></On></Sampler>
            </Device>
          </AbletonDevicePreset>
        </DevicePresets>
      </DrumBranchPreset>
    </BranchPresets>
  </GroupDevicePreset>
</Ableton>
"""


@pytest.fixture
def instrument_rack(tmp_path):
    p = tmp_path / "Synth Rack.adg"
    p.write_bytes(gzip.compress(_INSTRUMENT_RACK_XML.encode("utf-8")))
    return str(p)


@pytest.fixture
def drum_rack(tmp_path):
    p = tmp_path / "Test Kit.adg"
    p.write_bytes(gzip.compress(_DRUM_RACK_XML.encode("utf-8")))
    return str(p)


# ── instrument rack ──────────────────────────────────────────────────


def test_parse_instrument_rack_basics(instrument_rack):
    data, err = r._load(instrument_rack)
    assert err is None
    assert data["name"] == "Synth Rack"
    assert data["rack_type"] == "Instrument Rack"
    assert data["is_drum_rack"] is False
    assert data["ableton_version"] == "5.12.0_12049"
    assert data["chain_count"] == 1
    assert data["device_count"] == 2  # Operator + Reverb


def test_instrument_rack_chain_and_devices(instrument_rack):
    data, _ = r._load(instrument_rack)
    chain = data["chains"][0]
    assert chain["name"] == "Lead"
    types = [d["type"] for d in chain["devices"]]
    assert types == ["Operator", "Reverb"]
    reverb = chain["devices"][1]
    assert reverb["standard_name"] == "Reverb"
    assert reverb["is_on"] is False  # On/Manual was false


def test_only_named_macros_are_kept(instrument_rack):
    data, _ = r._load(instrument_rack)
    assert len(data["macros"]) == 1
    macro = data["macros"][0]
    assert macro["name"] == "Cutoff"
    assert macro["value"] == 64.0
    assert macro["index"] == 0


def test_instrument_rack_edition_is_suite(instrument_rack):
    # Operator is Suite-only, so the whole rack requires Suite.
    data, _ = r._load(instrument_rack)
    assert data["edition"] == "suite"
    assert "Operator" in data["suite_devices"]
    assert "Reverb" in data["standard_devices"]


# ── drum rack ────────────────────────────────────────────────────────


def test_drum_rack_pad_note_mapping(drum_rack):
    data, err = r._load(drum_rack)
    assert err is None
    assert data["is_drum_rack"] is True
    pads = data["drum_pads"]
    assert len(pads) == 2
    kick, snare = pads  # sorted ascending by midi note
    assert kick["midi_note"] == 36  # 128 - 92
    assert kick["note_name"] == "C1"
    assert kick["pad_label"] == "C1 (Kick)"
    assert kick["sample"] == "Kick.wav"
    assert snare["midi_note"] == 38  # 128 - 90


def test_drum_rack_samples_and_edition(drum_rack):
    data, _ = r._load(drum_rack)
    sample_names = {s["name"] for s in data["samples"]}
    assert "Kick.wav" in sample_names
    rel = {s["relative_path"] for s in data["samples"]}
    assert "Samples/Kick.wav" in rel
    # The Sampler pad is a Suite-only device.
    assert data["edition"] == "suite"
    assert "Sampler" in data["suite_devices"]


# ── tools return JSON ────────────────────────────────────────────────


def test_tools_return_valid_json(instrument_rack):
    summary = json.loads(adg_summary(None, instrument_rack))
    assert summary["name"] == "Synth Rack"
    assert summary["device_count"] == 2
    assert summary["macro_count"] == 1
    assert summary["edition"] == "suite"

    full = json.loads(adg_analyze(None, instrument_rack))
    assert full["chains"][0]["name"] == "Lead"
    assert [m["name"] for m in full["macros"]] == ["Cutoff"]

    edition = json.loads(adg_edition(None, instrument_rack))
    assert edition["edition"] == "suite"
    assert "Operator" in edition["suite_devices"]


# ── error handling ───────────────────────────────────────────────────


def test_bad_gzip_returns_friendly_error(tmp_path):
    bad = tmp_path / "not_a_rack.adg"
    bad.write_bytes(b"this is plain text, not gzip")
    data, err = r._load(str(bad))
    assert data is None
    assert "not a valid gzip" in err


def test_missing_file_returns_friendly_error(tmp_path):
    data, err = r._load(str(tmp_path / "nope.adg"))
    assert data is None
    assert "No file at" in err


def test_tool_raises_valueerror_on_bad_file(tmp_path):
    bad = tmp_path / "broken.adg"
    bad.write_bytes(b"nonsense")
    with pytest.raises(ValueError):
        adg_summary(None, str(bad))


# ── real factory drum rack (optional) ────────────────────────────────

_REAL_ADG_GLOBS = [
    "/Users/wouterstierhout/Music/Ableton/Factory Packs/Chop and Swing/Drums/Wah Soul Kit.adg",
    "/Users/wouterstierhout/Music/Ableton/Factory Packs/**/Drums/*.adg",
]


def _find_real_drum_rack():
    for pattern in _REAL_ADG_GLOBS:
        for hit in sorted(glob.glob(pattern, recursive=True)):
            return hit
    return None


def test_real_factory_drum_rack_parses():
    path = _find_real_drum_rack()
    if path is None:
        pytest.skip("no factory drum-rack .adg installed on this machine")
    data, err = r._load(path)
    assert err is None, err
    assert data["is_drum_rack"] is True
    assert len(data["drum_pads"]) > 0
    # Every pad resolves to a real MIDI note and most reference a sample.
    for pad in data["drum_pads"]:
        assert pad["midi_note"] is None or 0 <= pad["midi_note"] <= 127
    assert any(p["sample"] for p in data["drum_pads"])
    assert data["edition"] in ("intro", "standard", "suite")
    # Tools run end-to-end on the real file without raising.
    json.loads(adg_summary(None, path))
    json.loads(adg_analyze(None, path))
