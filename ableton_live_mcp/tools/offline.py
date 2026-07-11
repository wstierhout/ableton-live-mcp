"""Offline analysis of saved ``.als`` files, no running Live required.

An ``.als`` file is gzip-compressed XML whose element layout shifts between Live
versions and is not officially documented, so parsing is best-effort and
tolerant: missing fields become ``None`` rather than raising. These tools read a
file path directly (no socket, no Ableton), so an agent can inspect a saved set,
diff two versioned ``_vN.als`` files, or lint a set for unfinished work with Live
closed. Treat results as a summary, not a faithful round-trip of the project.
"""

import gzip
import json
import os
import zlib
from xml.etree import ElementTree as ET

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp

# Track element tag -> our kind label. Live 12 renamed Master to Main.
_TRACK_KIND = {
    "MidiTrack": "midi",
    "AudioTrack": "audio",
    "ReturnTrack": "return",
    "GroupTrack": "group",
    "MasterTrack": "master",
    "MainTrack": "master",
}

# ── tolerant value readers (Live stores most fields as <Foo Value="..."/>) ──


def _val(elem, default=None):
    if elem is None:
        return default
    v = elem.get("Value")
    if v is not None:
        return v
    if elem.text and elem.text.strip():
        return elem.text.strip()
    return default


def _fnum(elem, default=None):
    try:
        return float(_val(elem))
    except (TypeError, ValueError):
        return default


def _inum(elem, default=None):
    v = _val(elem)
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def _flag(elem, default=False):
    v = _val(elem)
    return default if v is None else v.strip().lower() == "true"


def _afloat(elem, name, default=None):
    try:
        return float(elem.get(name))
    except (TypeError, ValueError):
        return default


def _aint(elem, name, default=None):
    v = elem.get(name)
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


# ── element extractors ──


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
            notes.append(
                {
                    "pitch": p,
                    "start": _afloat(ev, "Time", 0.0),
                    "duration": _afloat(ev, "Duration", 0.0),
                    "velocity": _afloat(ev, "Velocity", 100.0),
                }
            )
    if not notes:  # fallback: notes directly under <Notes> with a Pitch attribute
        for ev in clip_elem.iter("MidiNoteEvent"):
            p = _aint(ev, "Pitch")
            if p is None:
                continue
            notes.append(
                {
                    "pitch": p,
                    "start": _afloat(ev, "Time", 0.0),
                    "duration": _afloat(ev, "Duration", 0.0),
                    "velocity": _afloat(ev, "Velocity", 100.0),
                }
            )
    return notes


def _clip(clip_elem, is_midi):
    start = _fnum(clip_elem.find("CurrentStart"), 0.0) or 0.0
    end = _fnum(clip_elem.find("CurrentEnd"), start) or start
    sample_path = None
    if not is_midi:
        ref = clip_elem.find(".//SampleRef/FileRef")
        if ref is not None:
            sample_path = (
                _val(ref.find("Path")) or _val(ref.find("RelativePath")) or _val(ref.find("Name"))
            )
    notes = _clip_notes(clip_elem) if is_midi else []
    return {
        "name": _val(clip_elem.find("Name"), "") or "",
        "is_midi": is_midi,
        "start": start,
        "length": round(max(0.0, end - start), 4),
        "looping": _flag(clip_elem.find("Loop/LoopOn")),
        "sample_path": sample_path,
        "note_count": len(notes),
        "notes": notes,
    }


def _clips(track_elem):
    clips = []
    for c in track_elem.iter():
        if "MidiClip" in c.tag:
            clips.append(_clip(c, True))
        elif "AudioClip" in c.tag:
            clips.append(_clip(c, False))
    return clips


def _devices(track_elem):
    """User-loaded devices from the track's inner DeviceChain.

    Live nests them as Track/DeviceChain/DeviceChain/Devices; a descendant search
    would also pick up FreezeSequencer and modulation Devices, so we walk the
    exact path.
    """
    outer = track_elem.find("DeviceChain")
    if outer is None:
        return []
    inner = outer.find("DeviceChain")
    chain = (inner.find("Devices") if inner is not None else None) or outer.find("Devices")
    if chain is None:
        return []
    devices = []
    for dev in list(chain):
        name = _val(dev.find(".//UserName")) or _val(dev.find(".//Name")) or dev.tag
        devices.append(
            {
                "name": name,
                "kind": dev.tag,
                "is_plugin": dev.tag
                in {"PluginDevice", "AuPluginDevice", "Vst3PluginDevice", "VstPluginDevice"},
            }
        )
    return devices


def _track(elem, index):
    kind = _TRACK_KIND.get(elem.tag, "unknown")
    name = _val(elem.find(".//Name/EffectiveName")) or _val(elem.find(".//Name/UserName")) or ""
    clips = _clips(elem) if kind in ("midi", "audio") else []
    return {
        "index": index,
        "kind": kind,
        "name": name,
        "muted": _flag(elem.find(".//Mute")),
        "soloed": _flag(elem.find(".//Solo")),
        "color_index": _inum(elem.find("ColorIndex")),
        "devices": _devices(elem),
        "clips": clips,
        "note_count": sum(c["note_count"] for c in clips),
    }


def _parse(path):
    """Parse an .als path into a plain dict. Raises FileNotFoundError / OSError /
    gzip.BadGzipFile / ET.ParseError, which the tools translate to clear errors."""
    with gzip.open(path, "rb") as f:
        root = ET.fromstring(f.read())
    live_set = root.find("LiveSet") or root

    tempo = (
        _fnum(live_set.find(".//Tempo/Manual"))
        or _fnum(live_set.find(".//MainTrack//Tempo/Manual"))
        or _fnum(live_set.find(".//MasterTrack//Tempo/Manual"), 120.0)
    )

    tracks = []
    parent = next(live_set.iter("Tracks"), None)
    if parent is not None:
        for i, t in enumerate(list(parent)):
            if t.tag in _TRACK_KIND:
                tracks.append(_track(t, i))

    master_elem = live_set.find("MainTrack") or live_set.find("MasterTrack")
    scenes = [_val(s.find("Name"), "") or "" for s in live_set.findall(".//Scenes/Scene")]

    return {
        "path": path,
        "creator": root.get("Creator"),
        "major_version": root.get("MajorVersion"),
        "minor_version": root.get("MinorVersion"),
        "tempo": tempo,
        "time_signature": "{}/{}".format(
            _inum(live_set.find(".//TimeSignatureNumerator"), 4),
            _inum(live_set.find(".//TimeSignatureDenominator"), 4),
        ),
        "tracks": tracks,
        "master": _track(master_elem, -1) if master_elem is not None else None,
        "scenes": scenes,
    }


def _keyed_by_name(tracks):
    """Key tracks by name, disambiguating duplicates as 'name #2', 'name #3', so a
    diff does not silently collapse same-named tracks to the last one."""
    seen = {}
    out = {}
    for t in tracks:
        name = t["name"]
        seen[name] = seen.get(name, 0) + 1
        out[name if seen[name] == 1 else f"{name} #{seen[name]}"] = t
    return out


def _load(path):
    """Parse with a friendly error string on failure. Returns (data, error)."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None, f"No file at {path}. Pass the full path to a .als file."
    try:
        return _parse(path), None
    except (gzip.BadGzipFile, EOFError, zlib.error):
        return None, f"{path} is not a valid gzip-compressed .als file."
    except ET.ParseError as e:
        return None, f"Could not parse the .als XML in {path}: {e}"
    except OSError as e:
        return None, f"Could not read {path}: {e}"


def _track_brief(t):
    return {
        "index": t["index"],
        "kind": t["kind"],
        "name": t["name"],
        "muted": t["muted"],
        "soloed": t["soloed"],
        "devices": [d["name"] for d in t["devices"]],
        "clips": len(t["clips"]),
        "notes": t["note_count"],
    }


# ── tools ──


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_summary(ctx: Context, path: str) -> str:
    """Summarize a saved .als file WITHOUT Live running: version/creator, tempo,
    time signature, and each track's kind, name, mute/solo, device names, clip
    count, and note count. `path` is a filesystem path to a .als file."""
    data, err = _load(path)
    if err:
        raise ValueError(err)
    return json.dumps(
        {
            "path": data["path"],
            "creator": data["creator"],
            "live_version": data["minor_version"],
            "tempo": data["tempo"],
            "time_signature": data["time_signature"],
            "track_count": len(data["tracks"]),
            "scene_count": len(data["scenes"]),
            "tracks": [_track_brief(t) for t in data["tracks"]],
        },
        indent=2,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_list_tracks(ctx: Context, path: str) -> str:
    """List every track in a saved .als file with its clips (name, MIDI/audio,
    start, length, looping, sample path, note count). No Live required."""
    data, err = _load(path)
    if err:
        raise ValueError(err)
    tracks = []
    for t in data["tracks"]:
        entry = _track_brief(t)
        entry["clips"] = [
            {
                k: c[k]
                for k in (
                    "name",
                    "is_midi",
                    "start",
                    "length",
                    "looping",
                    "sample_path",
                    "note_count",
                )
            }
            for c in t["clips"]
        ]
        tracks.append(entry)
    return json.dumps({"path": data["path"], "tracks": tracks}, indent=2)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_extract_midi(ctx: Context, path: str, track_index: int) -> str:
    """Extract MIDI notes from a track in a saved .als file, as
    {pitch,start,duration,velocity} dicts per clip. `track_index` is the track's
    position in the set (see als_summary). No Live required."""
    data, err = _load(path)
    if err:
        raise ValueError(err)
    match = next((t for t in data["tracks"] if t["index"] == track_index), None)
    if match is None:
        raise ValueError(
            f"No track at index {track_index}; the set has {len(data['tracks'])} tracks."
        )
    clips = [
        {"name": c["name"], "start": c["start"], "length": c["length"], "notes": c["notes"]}
        for c in match["clips"]
        if c["is_midi"]
    ]
    return json.dumps({"track": match["name"], "clips": clips}, indent=2)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_diff(ctx: Context, path_a: str, path_b: str) -> str:
    """Diff two saved .als files (e.g. song_v4.als vs song_v5.als) offline:
    tempo/time-signature changes, tracks added/removed by name, and per-track
    deltas in device chain, clip count, note count, and mute state. Great for
    logging what changed between versions."""
    a, err_a = _load(path_a)
    if err_a:
        raise ValueError(err_a)
    b, err_b = _load(path_b)
    if err_b:
        raise ValueError(err_b)

    changes = {}
    if a["tempo"] != b["tempo"]:
        changes["tempo"] = {"from": a["tempo"], "to": b["tempo"]}
    if a["time_signature"] != b["time_signature"]:
        changes["time_signature"] = {"from": a["time_signature"], "to": b["time_signature"]}

    by_name_a = _keyed_by_name(a["tracks"])
    by_name_b = _keyed_by_name(b["tracks"])
    changes["tracks_added"] = [n for n in by_name_b if n not in by_name_a]
    changes["tracks_removed"] = [n for n in by_name_a if n not in by_name_b]

    modified = []
    for name in by_name_a.keys() & by_name_b.keys():
        ta, tb = by_name_a[name], by_name_b[name]
        delta = {}
        da = [d["name"] for d in ta["devices"]]
        db = [d["name"] for d in tb["devices"]]
        if da != db:
            delta["devices"] = {"from": da, "to": db}
        if len(ta["clips"]) != len(tb["clips"]):
            delta["clip_count"] = {"from": len(ta["clips"]), "to": len(tb["clips"])}
        if ta["note_count"] != tb["note_count"]:
            delta["note_count"] = {"from": ta["note_count"], "to": tb["note_count"]}
        if ta["muted"] != tb["muted"]:
            delta["muted"] = {"from": ta["muted"], "to": tb["muted"]}
        if delta:
            modified.append({"track": name, **delta})
    changes["tracks_modified"] = modified
    return json.dumps({"a": path_a, "b": path_b, "changes": changes}, indent=2)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_find_unfinished(ctx: Context, path: str) -> str:
    """Lint a saved .als for likely-unfinished work, offline: no master
    limiter/compressor, MIDI track with no instrument, MIDI clip with no notes,
    empty audio track, or muted tracks. Returns machine-readable issues so an
    agent can decide what to fix before rendering."""
    data, err = _load(path)
    if err:
        raise ValueError(err)
    issues = []

    master_devs = " ".join(
        d["name"].lower() + " " + d["kind"].lower()
        for d in (data["master"] or {}).get("devices", [])
    )
    if not any(w in master_devs for w in ("limiter", "compressor", "maximizer")):
        issues.append(
            {
                "code": "no_master_dynamics",
                "severity": "warn",
                "message": "Master has no limiter or compressor.",
            }
        )

    for t in data["tracks"]:
        if t["kind"] == "midi" and not t["devices"]:
            issues.append(
                {
                    "code": "midi_no_instrument",
                    "severity": "warn",
                    "track": t["name"],
                    "message": f"MIDI track '{t['name']}' has no instrument device.",
                }
            )
        if t["kind"] == "midi":
            for c in t["clips"]:
                if c["is_midi"] and c["note_count"] == 0:
                    issues.append(
                        {
                            "code": "empty_midi_clip",
                            "severity": "info",
                            "track": t["name"],
                            "message": f"MIDI clip '{c['name']}' on '{t['name']}' has no notes.",
                        }
                    )
        if t["kind"] == "audio" and not t["clips"]:
            issues.append(
                {
                    "code": "empty_audio_track",
                    "severity": "info",
                    "track": t["name"],
                    "message": f"Audio track '{t['name']}' has no clips.",
                }
            )
        if t["muted"]:
            issues.append(
                {
                    "code": "muted_track",
                    "severity": "info",
                    "track": t["name"],
                    "message": f"Track '{t['name']}' is muted.",
                }
            )

    return json.dumps(
        {"path": data["path"], "issue_count": len(issues), "issues": issues}, indent=2
    )
