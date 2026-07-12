"""Offline analysis of saved ``.adg`` racks and ``.adv`` device presets.

An ``.adg`` (rack / group preset) or ``.adv`` (single device preset) is gzip-
compressed XML rooted at ``<Ableton>``. A rack wraps a ``GroupDevicePreset``
whose ``Device`` holds one group device (Instrument / Audio Effect / MIDI Effect
/ Drum rack) and whose ``BranchPresets`` hold the chains; each chain's
``DevicePresets`` nest further devices, so racks can contain racks. A ``.adv``
carries the device element directly under the root.

Like the ``.als`` tools in ``offline.py`` this reads a file path directly (no
socket, no running Live), so an agent can inspect a preset it is about to load:
what devices it contains, which Live edition it needs, the macro layout, the
samples it references, and — for drum racks — the pad-to-note map. The element
layout is undocumented and shifts between Live versions, so parsing is best-
effort and tolerant: missing fields become ``None`` rather than raising.
"""

import gzip
import json
import os
import re
import zlib
from xml.etree import ElementTree as ET

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ._als_xml import _afloat, _aint, _flag, _fnum, _inum, _val

# Group device tag -> the branch-preset tag that holds its chains.
_BRANCH_TYPE = {
    "InstrumentGroupDevice": "InstrumentBranchPreset",
    "DrumGroupDevice": "DrumBranchPreset",
    "AudioEffectGroupDevice": "AudioEffectBranchPreset",
    "MidiEffectGroupDevice": "MidiEffectBranchPreset",
}
_GROUP_TAGS = set(_BRANCH_TYPE)

# Internal device class tag -> friendly display name. Covers the aliases Live
# uses on disk (InstrumentVector = Wavetable, OriginalSimpler = Simpler, ...) so
# both display names and edition detection line up with what the UI shows.
_DEVICE_NAMES = {
    # group devices
    "InstrumentGroupDevice": "Instrument Rack",
    "DrumGroupDevice": "Drum Rack",
    "AudioEffectGroupDevice": "Audio Effect Rack",
    "MidiEffectGroupDevice": "MIDI Effect Rack",
    # instruments
    "OriginalSimpler": "Simpler",
    "Simpler": "Simpler",
    "MultiSampler": "Sampler",
    "Sampler": "Sampler",
    "InstrumentImpulse": "Impulse",
    "Impulse": "Impulse",
    "Operator": "Operator",
    "InstrumentVector": "Wavetable",
    "Wavetable": "Wavetable",
    "UltraAnalog": "Analog",
    "Analog": "Analog",
    "Collision": "Collision",
    "StringStudio": "Tension",
    "Tension": "Tension",
    "Electric": "Electric",
    "Lounge": "Electric",
    "GranulatorIII": "Granulator III",
    "Granulator": "Granulator III",
    "Meld": "Meld",
    "Drift": "Drift",
    "Bass": "Bass",
    "Poli": "Poli",
    "DrumSampler": "Drum Sampler",
    "DrumCell": "Drum Sampler",
    "MxDeviceInstrument": "Max Instrument",
    "MxDeviceAudioEffect": "Max Audio Effect",
    "MxDeviceMidiEffect": "Max MIDI Effect",
    "ExternalInstrument": "External Instrument",
    # audio effects
    "Eq8": "EQ Eight",
    "EQEight": "EQ Eight",
    "FilterEQ3": "EQ Three",
    "Eq3": "EQ Three",
    "ChannelEq": "Channel EQ",
    "Compressor2": "Compressor",
    "Compressor": "Compressor",
    "GlueCompressor": "Glue Compressor",
    "MultibandDynamics": "Multiband Dynamics",
    "AutoFilter": "Auto Filter",
    "AutoPan": "Auto Pan",
    "Reverb": "Reverb",
    "HybridReverb": "Hybrid Reverb",
    "Delay": "Delay",
    "Echo": "Echo",
    "GrainDelay": "Grain Delay",
    "FilterDelay": "Filter Delay",
    "Chorus": "Chorus-Ensemble",
    "Chorus2": "Chorus-Ensemble",
    "Saturator": "Saturator",
    "Overdrive": "Overdrive",
    "Redux": "Redux",
    "Redux2": "Redux",
    "Roar": "Roar",
    "Corpus": "Corpus",
    "Resonators": "Resonators",
    "Vocoder": "Vocoder",
    "DrumBuss": "Drum Buss",
    "Gate": "Gate",
    "Limiter": "Limiter",
    "Utility": "Utility",
    "StereoGain": "Utility",
    "BeatRepeat": "Beat Repeat",
    "Looper": "Looper",
    "Phaser": "Phaser",
    "PhaserNew": "Phaser-Flanger",
    "Flanger": "Flanger",
    "SpectralResonator": "Spectral Resonator",
    "Shifter": "Shifter",
    "Amp": "Amp",
    "Cabinet": "Cabinet",
    "Pedal": "Pedal",
    # midi effects
    "Arpeggiator": "Arpeggiator",
    "Chord": "Chord",
    "Scale": "Scale",
    "MidiPitcher": "Pitch",
    "NoteLength": "Note Length",
    "Random": "Random",
}

# Edition gate. Normalise a device (tag or display name) to lowercase letters
# only, then classify. Suite wins over Standard wins over Intro. Kept close to
# what the Live editions actually bundle; extend rather than rework.
_SUITE_ONLY = {
    "operator",
    "wavetable",
    "instrumentvector",
    "echo",
    "hybridreverb",
    "drift",
    "meld",
    "granulator",
    "granulatoriii",
    "corpus",
    "electric",
    "tension",
    "stringstudio",
    "analog",
    "ultraanalog",
    "collision",
    "sampler",
    "multisampler",
    "poli",
    "roar",
    "bass",
    "amp",
    "cabinet",
    "pedal",
    "resonators",
    "vocoder",
    "spectralresonator",
}
_STANDARD_ONLY = {
    "eqeight",
    "eqthree",
    "compressor",
    "simpler",
    "originalsimpler",
    "impulse",
    "drumrack",
    "drumgroupdevice",
    "drumsampler",
    "drumcell",
    "autofilter",
    "autopan",
    "reverb",
    "delay",
    "graindelay",
    "filterdelay",
    "chorus",
    "chorusensemble",
    "phaser",
    "phaserflanger",
    "flanger",
    "gate",
    "limiter",
    "saturator",
    "overdrive",
    "redux",
    "beatrepeat",
    "looper",
    "gluecompressor",
    "drumbuss",
    "multibanddynamics",
    "utility",
    "shifter",
}

# Standard drum-rack pad labels, keyed by MIDI note (C1 = 36 convention).
_PAD_LABELS = {
    36: "C1 (Kick)",
    37: "C#1 (Rim)",
    38: "D1 (Snare)",
    39: "D#1 (Clap)",
    40: "E1 (Snare Alt)",
    42: "F#1 (Hat Closed)",
    44: "G#1 (Hat Pedal)",
    46: "A#1 (Hat Open)",
    49: "C#2 (Crash)",
    51: "D#2 (Ride)",
}
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _norm(text):
    """Lowercase letters only, e.g. 'EQ Eight' -> 'eqeight', 'Eq8' -> 'eq'."""
    return re.sub(r"[^a-z]", "", (text or "").lower())


def _note_name(midi):
    """MIDI note -> Ableton-style name (60 = C3, so 36 = C1)."""
    if midi is None:
        return None
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 2}"


# ── device / chain extraction ──


def _device_elem(preset):
    """The actual device element held under a preset's ``Device`` wrapper."""
    holder = preset.find("Device")
    if holder is None:
        return None
    kids = list(holder)
    return kids[0] if kids else None


def _sample_ref(dev_elem):
    """First sample this device points at: {name, relative_path, path}."""
    ref = dev_elem.find(".//SampleRef/FileRef")
    if ref is None:
        return None
    name = _val(ref.find("Name"))
    rel = _val(ref.find("RelativePath"))
    path = _val(ref.find("Path"))
    if name is None and rel:
        name = rel.rsplit("/", 1)[-1]
    if name is None and path:
        name = path.rsplit("/", 1)[-1]
    return {"name": name, "relative_path": rel, "path": path}


def _classify(tag, standard_name):
    """'suite' | 'standard' | None for one device, by tag or display name."""
    keys = {_norm(tag), _norm(standard_name)}
    if keys & _SUITE_ONLY:
        return "suite"
    if keys & _STANDARD_ONLY:
        return "standard"
    return None


def _device_info(dev_elem):
    tag = dev_elem.tag
    standard = _DEVICE_NAMES.get(tag, tag)
    user_name = _val(dev_elem.find("UserName"))
    if user_name:
        user_name = user_name.strip()
    display = user_name if (user_name and user_name != standard) else standard
    info = {
        "type": tag,
        "name": display,
        "standard_name": standard,
        "is_on": _flag(dev_elem.find("On/Manual"), True),
        "is_rack": tag in _GROUP_TAGS,
        "edition": _classify(tag, standard),
    }
    return info


def _branch_presets(preset, dev_elem):
    """A group's BranchPresets, whether at preset level (.adg) or on the device
    element itself (rare / .adv)."""
    bp = preset.find("BranchPresets") if preset is not None else None
    if bp is None:
        bp = dev_elem.find("BranchPresets")
    return bp


def _parse_preset(preset, depth):
    """One device (with nested chains if it is a rack) from a *Preset element."""
    if depth > 15:
        return None
    dev_elem = _device_elem(preset)
    if dev_elem is None:
        return None
    info = _device_info(dev_elem)
    if info["is_rack"]:
        info["chains"] = _parse_chains(preset, dev_elem, depth)
    else:
        sample = _sample_ref(dev_elem)
        if sample:
            info["sample"] = sample
    return info


def _parse_chains(preset, dev_elem, depth):
    """Chains of a group device, from its BranchPresets."""
    chains = []
    bp = _branch_presets(preset, dev_elem)
    if bp is None:
        return chains
    branch_tag = _BRANCH_TYPE.get(dev_elem.tag)
    if branch_tag is None:
        return chains
    is_drum = branch_tag == "DrumBranchPreset"
    for idx, branch in enumerate(bp.findall(branch_tag)):
        chains.append(_parse_branch(branch, idx, is_drum, depth + 1))
    return chains


def _parse_branch(branch, index, is_drum, depth):
    chain = {
        "index": index,
        "name": _val(branch.find("Name"), "") or "",
        "is_soloed": _flag(branch.find("IsSoloed")),
        "devices": [],
    }
    if is_drum:
        chain.update(_drum_pad_fields(branch))
    presets = branch.find("DevicePresets")
    if presets is not None:
        for child in presets:
            if child.find("Device") is None:
                continue
            dev = _parse_preset(child, depth + 1)
            if dev:
                chain["devices"].append(dev)
    return chain


def _drum_pad_fields(branch):
    """Pad-specific fields for a DrumBranchPreset. Live stores the trigger note
    as ``ReceivingNote`` inverted: the actual MIDI note is 128 - ReceivingNote
    (a Golden-Era kick at ReceivingNote 92 lands on note 36 = C1)."""
    fields = {}
    recv = _inum(branch.find(".//ReceivingNote"))
    midi = 128 - recv if recv is not None else None
    fields["receiving_note"] = recv
    fields["midi_note"] = midi
    fields["note_name"] = _note_name(midi)
    fields["pad_label"] = _PAD_LABELS.get(midi)
    choke = branch.find(".//ChokeGroup")
    fields["choke_group"] = _inum(choke)
    key = branch.find("KeyRange")
    if key is not None:
        fields["key_range"] = [_inum(key.find("Min")), _inum(key.find("Max"))]
    vel = branch.find("VelocityRange")
    if vel is not None:
        fields["velocity_range"] = [_inum(vel.find("Min")), _inum(vel.find("Max"))]
    return fields


def _parse_macros(dev_elem):
    """Named macro controls of the main device: {index, name, value}. Only
    macros the user actually named (not the auto 'Macro N' defaults) are kept."""
    macros = []
    if dev_elem is None:
        return macros
    for i in range(16):
        name_elem = dev_elem.find(f"MacroDisplayNames.{i}")
        if name_elem is None:
            continue
        name = _val(name_elem)
        if not name or name == f"Macro {i + 1}":
            continue
        value = _fnum(dev_elem.find(f"MacroControls.{i}/Manual"))
        macros.append({"index": i, "name": name, "value": value})
    return macros


def _macro_mappings(root):
    """Legacy macro->parameter mappings from MacroControlTarget / MacroMappings.
    Present in older sets; modern Live 12 stores this elsewhere, so this is
    commonly empty (best-effort, not a failure)."""
    mappings = []
    for tag in ("MacroControlTarget", "MacroMappings"):
        for m in root.iter(tag):
            entry = {
                "macro_index": _aint(m, "MacroIndex"),
                "parameter_id": m.get("ParameterId") or _val(m.find("ParameterId")),
                "min": _afloat(m, "Min"),
                "max": _afloat(m, "Max"),
            }
            if any(v is not None for v in entry.values()):
                mappings.append(entry)
    return mappings


def _all_devices(chains):
    """Every device in a chain tree, flattened (racks included), recursively."""
    out = []
    for chain in chains:
        for dev in chain.get("devices", []):
            out.append(dev)
            if dev.get("is_rack"):
                out.extend(_all_devices(dev.get("chains", [])))
    return out


def _edition(devices):
    """Classify the required Live edition and note which devices forced it."""
    suite = [d["name"] for d in devices if d.get("edition") == "suite"]
    standard = [d["name"] for d in devices if d.get("edition") == "standard"]
    if suite:
        edition = "suite"
    elif standard:
        edition = "standard"
    else:
        edition = "intro"
    return edition, sorted(set(suite)), sorted(set(standard))


def _drum_pads(root):
    """Flat pad map across the whole rack: every DrumBranchPreset with its note
    and the sample it triggers (sorted by MIDI note)."""
    pads = []
    for branch in root.iter("DrumBranchPreset"):
        fields = _drum_pad_fields(branch)
        dev_elem = branch.find(".//DevicePresets//Device/*")
        sample = _sample_ref(branch)
        pads.append(
            {
                "name": _val(branch.find("Name"), "") or "",
                "midi_note": fields["midi_note"],
                "note_name": fields["note_name"],
                "receiving_note": fields["receiving_note"],
                "pad_label": fields["pad_label"],
                "choke_group": fields["choke_group"],
                "device": _DEVICE_NAMES.get(dev_elem.tag, dev_elem.tag)
                if dev_elem is not None
                else None,
                "sample": sample["name"] if sample else None,
                "sample_path": sample["relative_path"] if sample else None,
            }
        )
    pads.sort(key=lambda p: (p["midi_note"] is None, p["midi_note"]))
    return pads


def _all_samples(root):
    """Every distinct sample referenced anywhere in the preset."""
    seen = set()
    samples = []
    for ref in root.iter("FileRef"):
        name = _val(ref.find("Name"))
        rel = _val(ref.find("RelativePath"))
        if name is None and rel:
            name = rel.rsplit("/", 1)[-1]
        if name is None and rel is None:
            continue
        key = (name, rel)
        if key in seen:
            continue
        seen.add(key)
        samples.append({"name": name, "relative_path": rel})
    return samples


# ── top-level parse ──


def _parse(path, detail=False):
    """Parse an .adg/.adv path into a plain dict. Raises OSError /
    gzip.BadGzipFile / zlib.error / ET.ParseError for _load to translate. When
    `detail` is set, also extract the (full-tree-walk) macro mappings and drum-pad
    map that only adg_analyze needs."""
    with gzip.open(path, "rb") as f:
        root = ET.fromstring(f.read())

    ableton = root if root.tag == "Ableton" else root.find(".//Ableton")
    if ableton is None:
        ableton = root
    version_major = ableton.get("MajorVersion")
    version_minor = ableton.get("MinorVersion")
    creator = ableton.get("Creator")

    group = root.find("GroupDevicePreset")
    if group is None:
        group = root.find(".//GroupDevicePreset")
    is_drum_rack = next(root.iter("DrumGroupDevice"), None) is not None

    if group is not None:
        main_dev = _device_elem(group)
        name = _val(group.find("Name")) or (
            _val(main_dev.find("UserName")) if main_dev is not None else None
        )
        root_device = _parse_preset(group, 0) or {}
        chains = root_device.get("chains", [])
        rack_type = _DEVICE_NAMES.get(main_dev.tag, main_dev.tag) if main_dev is not None else None
    else:
        # Single-device .adv: the device element sits directly under the root.
        main_dev = next((c for c in root if c.tag not in ("LiveSet",) and c.tag != "Ableton"), None)
        name = _val(main_dev.find("UserName")) if main_dev is not None else None
        if main_dev is not None:
            root_device = _device_info(main_dev)
            if root_device["is_rack"]:
                root_device["chains"] = _parse_chains(None, main_dev, 0)
            else:
                sample = _sample_ref(main_dev)
                if sample:
                    root_device["sample"] = sample
        else:
            root_device = {}
        chains = root_device.get("chains", [])
        rack_type = root_device.get("standard_name")

    if not name:
        name = os.path.splitext(os.path.basename(path))[0]

    devices = _all_devices(chains)
    # A single-device .adv has no chains: judge the edition off the lone device.
    if not devices and root_device:
        devices = [root_device]
    edition, suite_devs, standard_devs = _edition(devices)

    result = {
        "path": path,
        "name": name,
        "creator": creator,
        "major_version": version_major,
        "minor_version": version_minor,
        "ableton_version": (
            f"{version_major}.{version_minor}" if version_major and version_minor else None
        ),
        "is_drum_rack": is_drum_rack,
        "rack_type": rack_type,
        "root_device": root_device,
        "chains": chains,
        "macros": _parse_macros(main_dev),
        "samples": _all_samples(root),
        "device_count": len(devices),
        "chain_count": len(chains),
        "edition": edition,
        "suite_devices": suite_devs,
        "standard_devices": standard_devs,
    }
    if detail:
        # Extra full-tree walks that only adg_analyze consumes.
        result["macro_mappings"] = _macro_mappings(root)
        result["drum_pads"] = _drum_pads(root) if is_drum_rack else []
    return result


def _load(path, detail=False):
    """Parse with a friendly error string on failure. Returns (data, error)."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None, f"No file at {path}. Pass the full path to a .adg or .adv file."
    try:
        return _parse(path, detail=detail), None
    except (gzip.BadGzipFile, EOFError, zlib.error):
        return None, f"{path} is not a valid gzip-compressed .adg/.adv file."
    except ET.ParseError as e:
        return None, f"Could not parse the preset XML in {path}: {e}"
    except OSError as e:
        return None, f"Could not read {path}: {e}"


# ── tools ──


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def adg_summary(ctx: Context, path: str) -> str:
    """Summarize a saved .adg rack or .adv device preset WITHOUT Live running:
    name, rack type, total device count, top-level chain count, named-macro
    count, required Live edition (intro/standard/suite), and whether it is a drum
    rack. `path` is a filesystem path to a .adg or .adv file."""
    data, err = _load(path)
    if err:
        raise ValueError(err)
    return json.dumps(
        {
            "path": data["path"],
            "name": data["name"],
            "rack_type": data["rack_type"],
            "ableton_version": data["ableton_version"],
            "device_count": data["device_count"],
            "chain_count": data["chain_count"],
            "macro_count": len(data["macros"]),
            "edition": data["edition"],
            "is_drum_rack": data["is_drum_rack"],
            "sample_count": len(data["samples"]),
        },
        indent=2,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def adg_analyze(ctx: Context, path: str) -> str:
    """Full offline analysis of a .adg rack or .adv preset: named macros
    (name/value), the chain + device tree (recursive, incl. racks-inside-racks
    with each device's kind/name/on-state and any sample), legacy macro->
    parameter mappings, every referenced sample (name + relative path), the
    required Live edition, and — for drum racks — the pad map (pad -> MIDI note,
    label, and sample). No Live required."""
    data, err = _load(path, detail=True)
    if err:
        raise ValueError(err)
    return json.dumps(
        {
            "path": data["path"],
            "name": data["name"],
            "rack_type": data["rack_type"],
            "ableton_version": data["ableton_version"],
            "is_drum_rack": data["is_drum_rack"],
            "edition": data["edition"],
            "device_count": data["device_count"],
            "chain_count": data["chain_count"],
            "macros": data["macros"],
            "macro_mappings": data["macro_mappings"],
            "chains": data["chains"],
            "samples": data["samples"],
            "drum_pads": data["drum_pads"],
        },
        indent=2,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def adg_edition(ctx: Context, path: str) -> str:
    """Classify the Live edition a saved .adg/.adv needs, offline: 'suite' if any
    Suite-only device is present (Operator, Wavetable, Echo, Drift, Meld,
    Sampler, Collision, ...), else 'standard' if any Standard-only device is
    present (EQ Eight, Compressor, Simpler, Impulse, Drum Rack, ...), else
    'intro'. Returns the verdict plus which devices drove it, so an agent can
    tell whether a preset will load on the user's edition before loading it."""
    data, err = _load(path)
    if err:
        raise ValueError(err)
    return json.dumps(
        {
            "path": data["path"],
            "name": data["name"],
            "edition": data["edition"],
            "suite_devices": data["suite_devices"],
            "standard_devices": data["standard_devices"],
            "device_count": data["device_count"],
        },
        indent=2,
    )
