"""Pure parsing layer for saved ``.als`` Live sets (no MCP tool definitions).

Kept separate from the tool module (`offline.py`) so other modules — e.g. the
key-detection tools — can parse a set without importing `offline.py`, whose
import registers its MCP tools and would defeat ABLETON_TOOLSETS group
isolation. Layout knowledge is best-effort: paths verified against Live 12
factory sets; missing fields become ``None`` rather than raising.
"""

import json
import threading
from collections import OrderedDict
from pathlib import Path

from ._als_xml import _afloat, _aint, _flag, _fnum, _inum, _val, load_gz_xml

# Track element tag -> our kind label. Live 12 renamed Master to Main.
_TRACK_KIND = {
    "MidiTrack": "midi",
    "AudioTrack": "audio",
    "ReturnTrack": "return",
    "GroupTrack": "group",
    "MasterTrack": "master",
    "MainTrack": "master",
}

# Rack tags whose branches hold further devices worth surfacing.
_RACK_TAGS = {
    "AudioEffectGroupDevice",
    "InstrumentGroupDevice",
    "MidiEffectGroupDevice",
    "DrumGroupDevice",
}

_PLUGIN_TAGS = {"PluginDevice", "AuPluginDevice", "Vst3PluginDevice", "VstPluginDevice"}


# ── element extractors ──


def _note_event(ev, pitch):
    return {
        "pitch": pitch,
        "start": _afloat(ev, "Time", 0.0),
        "duration": _afloat(ev, "Duration", 0.0),
        "velocity": _afloat(ev, "Velocity", 100.0),
    }


def _clip_notes(clip_elem):
    """Return note dicts for a MIDI clip in our {pitch,start,duration,velocity} shape.

    Handles both Live shapes: KeyTrack/MidiKey grouping and flat MidiNoteEvent
    rows that carry an explicit Pitch attribute.
    """
    notes = []
    for kt in clip_elem.iter("KeyTrack"):
        pitch = _inum(kt.find("MidiKey"))
        for ev in kt.iter("MidiNoteEvent"):
            p = pitch if pitch is not None else _aint(ev, "Pitch", 60)
            notes.append(_note_event(ev, p))
    if not notes:  # fallback: notes directly under <Notes> with a Pitch attribute
        for ev in clip_elem.iter("MidiNoteEvent"):
            p = _aint(ev, "Pitch")
            if p is not None:
                notes.append(_note_event(ev, p))
    return notes


def _clip(clip_elem, is_midi, include_notes=True):
    start = _fnum(clip_elem.find("CurrentStart"), 0.0) or 0.0
    end = _fnum(clip_elem.find("CurrentEnd"), start) or start
    sample_path = None
    if not is_midi:
        ref = clip_elem.find(".//SampleRef/FileRef")
        if ref is not None:
            sample_path = (
                _val(ref.find("Path")) or _val(ref.find("RelativePath")) or _val(ref.find("Name"))
            )
    notes = _clip_notes(clip_elem) if is_midi and include_notes else []
    count = len(notes)
    if is_midi and not include_notes:
        grouped = sum(1 for kt in clip_elem.iter("KeyTrack") for _ in kt.iter("MidiNoteEvent"))
        count = grouped or sum(
            1 for ev in clip_elem.iter("MidiNoteEvent") if _aint(ev, "Pitch") is not None
        )
    return {
        "name": _val(clip_elem.find("Name"), "") or "",
        "is_midi": is_midi,
        "start": start,
        "length": round(max(0.0, end - start), 4),
        "looping": _flag(clip_elem.find("Loop/LoopOn")),
        "sample_path": sample_path,
        "note_count": count,
        "notes": notes,
    }


def _clips(track_elem, include_notes=True):
    """Session and arrangement clips of a track. Skips the FreezeSequencer
    subtree, whose stored freeze clips would otherwise count as phantom clips
    on frozen tracks, and does not descend into matched clips (they don't nest,
    and their note subtrees can hold tens of thousands of elements)."""
    clips = []
    stack = list(track_elem)
    while stack:
        e = stack.pop()
        if e.tag == "FreezeSequencer":
            continue
        if "MidiClip" in e.tag:
            clips.append(_clip(e, True, include_notes))
        elif "AudioClip" in e.tag:
            clips.append(_clip(e, False, include_notes))
        else:
            stack.extend(e)
    return clips


def _device_name(dev):
    """A device's display name: the user rename, a plugin's own name, or the tag."""
    name = _val(dev.find("UserName"))
    if not name and dev.tag in _PLUGIN_TAGS:
        desc = dev.find("PluginDesc")
        if desc is not None:
            name = _val(desc.find(".//PlugName")) or _val(desc.find(".//Name"))
    if not name:
        name = _val(dev.find("Name"))
    return name or dev.tag


def _devices(track_elem):
    """User-loaded devices from the track's inner DeviceChain, with rack
    contents flattened in (marked ``in_rack``) so nothing hides inside a rack.

    Live nests them as Track/DeviceChain/DeviceChain/Devices; a descendant search
    would also pick up FreezeSequencer and modulation Devices, so we walk the
    exact path.
    """
    outer = track_elem.find("DeviceChain")
    if outer is None:
        return []
    inner = outer.find("DeviceChain")
    chain = inner.find("Devices") if inner is not None else None
    if chain is None:
        chain = outer.find("Devices")
    if chain is None:
        return []
    devices = []
    for dev in list(chain):
        devices.append(
            {
                "name": _device_name(dev),
                "kind": dev.tag,
                "is_plugin": dev.tag in _PLUGIN_TAGS,
            }
        )
        if dev.tag in _RACK_TAGS:
            for sub in dev.findall(".//Devices/*"):
                devices.append(
                    {
                        "name": _device_name(sub),
                        "kind": sub.tag,
                        "is_plugin": sub.tag in _PLUGIN_TAGS,
                        "in_rack": True,
                    }
                )
    return devices


def _track(elem, index, include_notes=True):
    kind = _TRACK_KIND.get(elem.tag, "unknown")
    name = _val(elem.find(".//Name/EffectiveName")) or _val(elem.find(".//Name/UserName")) or ""
    clips = _clips(elem, include_notes) if kind in ("midi", "audio") else []
    # Mute is stored inverted as the track activator (Speaker true = audible).
    # SoloSink is the track-level solo field; treat as best-effort.
    mixer_speaker = elem.find("DeviceChain/Mixer/Speaker/Manual")
    color = elem.find("Color")  # Live 11/12 tag; ColorIndex is the Live <=10 one
    return {
        "index": index,
        "kind": kind,
        "name": name,
        "muted": not _flag(mixer_speaker, True),
        "soloed": _flag(elem.find("DeviceChain/Mixer/SoloSink")),
        "color_index": _inum(color) if color is not None else _inum(elem.find("ColorIndex")),
        "volume": _fnum(elem.find("DeviceChain/Mixer/Volume/Manual")),
        "pan": _fnum(elem.find("DeviceChain/Mixer/Pan/Manual")),
        "automation_lanes": len(elem.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope")),
        "devices": _devices(elem),
        "clips": clips,
        "note_count": sum(c["note_count"] for c in clips),
    }


def _parse_root(root, path, include_notes=True):
    """Extract a plain dict from a parsed <Ableton> root."""
    live_set = root.find("LiveSet")
    if live_set is None:
        live_set = root

    tempo = _fnum(live_set.find(".//Tempo/Manual"))
    if tempo is None:
        tempo = _fnum(live_set.find(".//MainTrack//Tempo/Manual"))
    if tempo is None:
        tempo = _fnum(live_set.find(".//MasterTrack//Tempo/Manual"), 120.0)

    tracks = []
    parent = next(live_set.iter("Tracks"), None)
    if parent is not None:
        for i, t in enumerate(list(parent)):
            if t.tag in _TRACK_KIND:
                tracks.append(_track(t, i, include_notes))

    master_elem = live_set.find("MainTrack")
    if master_elem is None:
        master_elem = live_set.find("MasterTrack")
    scenes = [_val(s.find("Name"), "") or "" for s in live_set.findall(".//Scenes/Scene")]
    locators = [
        {"time": _fnum(loc.find("Time")), "name": _val(loc.find("Name"), "") or ""}
        for loc in live_set.findall(".//Locators/Locators/Locator")
    ]

    return {
        "path": path,
        "creator": root.get("Creator"),
        "minor_version": root.get("MinorVersion"),
        "tempo": tempo,
        "time_signature": "{}/{}".format(
            _inum(live_set.find(".//TimeSignatureNumerator"), 4),
            _inum(live_set.find(".//TimeSignatureDenominator"), 4),
        ),
        "tracks": tracks,
        "master": _track(master_elem, -1, include_notes) if master_elem is not None else None,
        "scenes": scenes,
        "locators": locators,
    }


_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_BYTES = 16 * 1024 * 1024


def _fingerprint(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _load(path, include_notes=True, refresh=False):
    """Stat-validated LRU; never expose shared mutable cached objects."""
    source = Path(path).expanduser().resolve()
    try:
        before = _fingerprint(source)
    except OSError:
        before = None
    key = (str(source), include_notes, before)
    payload = None
    with _CACHE_LOCK:
        if not refresh and key in _CACHE:
            payload = _CACHE[key]
            _CACHE.move_to_end(key)
    if payload is not None:
        try:
            unchanged = _fingerprint(source) == before
        except OSError:
            unchanged = False
        if unchanged:
            return json.loads(payload), None
        return None, "The .als file changed while reading it; retry after saving completes"
    root, resolved, err = load_gz_xml(source, noun=".als")
    if err:
        return None, err
    data = _parse_root(root, resolved, include_notes)
    try:
        after = _fingerprint(source)
    except OSError:
        after = None
    if before != after:
        return None, "The .als file changed while reading it; retry after saving completes"
    payload = json.dumps(data).encode("utf-8")
    if before is not None and len(payload) <= _CACHE_BYTES:
        with _CACHE_LOCK:
            for old in list(_CACHE):
                if old[0] == str(source) and old[2] != before:
                    del _CACHE[old]
            _CACHE[key] = payload
            _CACHE.move_to_end(key)
            while len(_CACHE) > 8 or sum(map(len, _CACHE.values())) > _CACHE_BYTES:
                _CACHE.popitem(last=False)
    return data, None
