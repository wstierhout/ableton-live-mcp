"""Offline analysis of saved ``.als`` files, no running Live required.

An ``.als`` file is gzip-compressed XML whose element layout shifts between Live
versions and is not officially documented, so parsing is best-effort and
tolerant: missing fields become ``None`` rather than raising. These tools read a
file path directly (no socket, no Ableton), so an agent can inspect a saved set,
diff two versioned ``_vN.als`` files, or lint a set for unfinished work with Live
closed. Treat results as a summary, not a faithful round-trip of the project.
"""

from mcp.types import ToolAnnotations

from ..app import mcp
from ._als_parse import _load
from ._util import keyed_by_name as _keyed_by_name


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
def als_summary(path: str, refresh: bool = False) -> dict:
    """Summarize a saved .als file WITHOUT Live running: version/creator, tempo,
    time signature, and each track's kind, name, mute/solo, device names, clip
    count, and note count. `path` is a filesystem path to a .als file."""
    data, err = _load(path, include_notes=False, refresh=refresh)
    if err:
        raise ValueError(err)
    return {
        "path": data["path"],
        "creator": data["creator"],
        "live_version": data["minor_version"],
        "tempo": data["tempo"],
        "time_signature": data["time_signature"],
        "track_count": len(data["tracks"]),
        "scene_count": len(data["scenes"]),
        "tracks": [_track_brief(t) for t in data["tracks"]],
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_list_tracks(path: str) -> dict:
    """List every track in a saved .als file with its clips (name, MIDI/audio,
    start, length, looping, sample path, note count). No Live required."""
    data, err = _load(path, include_notes=False)
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
    return {"path": data["path"], "tracks": tracks}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_extract_midi(path: str, track_index: int) -> dict:
    """Extract MIDI notes from a track in a saved .als file, as
    {pitch,start,duration,velocity} dicts per clip. `track_index` is the track's
    position in the set (see als_summary). Exports at most 32,000 notes;
    larger tracks fail explicitly. No Live required."""
    data, err = _load(path)
    if err:
        raise ValueError(err)
    match = next((t for t in data["tracks"] if t["index"] == track_index), None)
    if match is None:
        raise ValueError(
            f"No track at index {track_index}; the set has {len(data['tracks'])} tracks."
        )
    if match["note_count"] > 32000:
        raise ValueError("Track exceeds the 32000-note export limit; use a smaller saved track")
    clips = [
        {"name": c["name"], "start": c["start"], "length": c["length"], "notes": c["notes"]}
        for c in match["clips"]
        if c["is_midi"]
    ]
    return {"track": match["name"], "clips": clips}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_diff(path_a: str, path_b: str) -> dict:
    """Diff two saved .als files (e.g. song_v4.als vs song_v5.als) offline:
    tempo/time-signature changes, tracks added/removed, and per-track deltas.
    Tracks match by literal name and occurrence in track order, not stable ID;
    reordering or renaming same-named tracks can make the comparison ambiguous."""
    a, err_a = _load(path_a, include_notes=False)
    if err_a:
        raise ValueError(err_a)
    b, err_b = _load(path_b, include_notes=False)
    if err_b:
        raise ValueError(err_b)

    changes = {}
    if a["tempo"] != b["tempo"]:
        changes["tempo"] = {"from": a["tempo"], "to": b["tempo"]}
    if a["time_signature"] != b["time_signature"]:
        changes["time_signature"] = {"from": a["time_signature"], "to": b["time_signature"]}

    by_name_a = _keyed_by_name(a["tracks"])
    by_name_b = _keyed_by_name(b["tracks"])
    labels = {key: key[0] for key in by_name_a | by_name_b if key[1] == 1}
    used = set(labels.values())
    for key in by_name_a | by_name_b:
        if key not in labels:
            suffix = key[1]
            label = f"{key[0]} #{suffix}"
            while label in used:
                suffix += 1
                label = f"{key[0]} #{suffix}"
            labels[key] = label
            used.add(label)
    changes["tracks_added"] = [labels[n] for n in by_name_b if n not in by_name_a]
    changes["tracks_removed"] = [labels[n] for n in by_name_a if n not in by_name_b]

    modified = []
    for name in by_name_a:
        if name not in by_name_b:
            continue
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
        for field in ("volume", "pan", "automation_lanes"):
            va, vb = ta.get(field), tb.get(field)
            if va != vb and not (va is None and vb is None):
                delta[field] = {"from": va, "to": vb}
        if delta:
            modified.append(
                {
                    "track": labels[name],
                    "name": name[0],
                    "occurrence": name[1],
                    "index_a": ta["index"],
                    "index_b": tb["index"],
                    **delta,
                }
            )
    changes["tracks_modified"] = modified
    if a.get("locators") != b.get("locators"):
        changes["locators"] = {"from": a.get("locators"), "to": b.get("locators")}
    return {"a": path_a, "b": path_b, "changes": changes}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_details(path: str) -> dict:
    """Read mixing and arrangement detail from a saved .als, offline: the set's
    locators (name + beat time) and each track's fader volume, pan, and number of
    automation lanes. Complements als_summary for reviewing a mix without Live."""
    data, err = _load(path, include_notes=False)
    if err:
        raise ValueError(err)
    tracks = [
        {
            "index": t["index"],
            "name": t["name"],
            "volume": t["volume"],
            "pan": t["pan"],
            "automation_lanes": t["automation_lanes"],
        }
        for t in data["tracks"]
    ]
    return {"path": data["path"], "locators": data["locators"], "tracks": tracks}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def als_find_unfinished(path: str) -> dict:
    """Suggest saved-set details to review: unrecognized master dynamics, empty
    MIDI device chains/clips, empty audio tracks and muted tracks. These are
    observations, not faults or a completeness score; external MIDI routing,
    third-party mastering and intentional silence are not evaluated."""
    data, err = _load(path, include_notes=False)
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
                "severity": "info",
                "message": "No master dynamics recognized by device name; dynamics may be "
                "unneeded or provided by a plugin with a different name.",
            }
        )

    for t in data["tracks"]:
        if t["kind"] == "midi" and not t["devices"]:
            issues.append(
                {
                    "code": "midi_no_instrument",
                    "severity": "info",
                    "track": t["name"],
                    "message": f"MIDI track '{t['name']}' has no devices; "
                    "it may intentionally route MIDI elsewhere.",
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

    return {"path": data["path"], "issue_count": len(issues), "issues": issues}
