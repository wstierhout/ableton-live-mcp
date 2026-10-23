"""Server-side analysis and orientation tools (no Live API risk).

`analyze_mix` reasons over a live session snapshot to flag likely mix problems;
`describe_capabilities` gives an agent a high-level map of the toolset before its
first call. Both are pure logic over data the other tools already return.
"""

import threading

from mcp.types import ToolAnnotations

from ..app import mcp
from ..audio_stats import NATIVE_AUDIO
from ..connection import get_ableton_connection
from ._groups import ENABLED_GROUPS, GROUP_DESCRIPTIONS

_CONVENTIONS = [
    "Indices are 0-based; times and lengths are in beats.",
    "Volume is Live's 0.0 to 1.0 fader range where 0.85 is 0 dB.",
    "add_notes_to_clip replaces a clip's notes; use edit_notes for incremental changes.",
    "Device parameters use each parameter's own range; read get_device_parameters first.",
    "Start/stop/seek replies include requested and observed state; check confirmed before continuing.",
]


def mix_findings(snapshot):
    """Flag likely mix issues from a get_session_snapshot result. Pure function
    so it can be unit-tested without Live. Returns a list of {code, severity,
    ...} dicts, most actionable first."""
    findings = []
    tracks = [t for t in snapshot.get("tracks", []) if isinstance(t, dict)]

    loud = [t.get("name") for t in tracks if not t.get("muted") and (t.get("volume") or 0) >= 0.9]
    if len(loud) >= 4:
        findings.append(
            {
                "code": "many_loud_tracks",
                "severity": "warn",
                "message": f"{len(loud)} unmuted tracks have faders >= 0.9: {loud}. Measure output before judging headroom; fader positions alone do not establish signal level.",
            }
        )

    if not any((not t.get("muted")) and (t.get("clips") or 0) > 0 for t in tracks):
        findings.append(
            {
                "code": "nothing_playing",
                "severity": "warn",
                "message": "No unmuted Session clips found. Arrangement playback, monitoring or live input may still produce audio.",
            }
        )

    for t in tracks:
        name = t.get("name")
        if t.get("type") == "midi" and not t.get("devices"):
            findings.append(
                {
                    "code": "midi_no_instrument",
                    "severity": "warn",
                    "track": name,
                    "message": f"MIDI track '{name}' has no reported devices. Check whether it intentionally routes MIDI to another instrument.",
                }
            )
        if t.get("muted"):
            findings.append(
                {
                    "code": "muted_track",
                    "severity": "info",
                    "track": name,
                    "message": f"Track '{name}' is muted.",
                }
            )
        if (t.get("clips") or 0) == 0 and t.get("devices"):
            findings.append(
                {
                    "code": "empty_track",
                    "severity": "info",
                    "track": name,
                    "message": f"Track '{name}' has devices but no Session clips.",
                }
            )
    return findings


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def analyze_mix() -> dict:
    """Heuristic session-state checks, NOT audio, peak, LUFS or headroom measurements.
    Scan the current live set for inspection leads: several high faders, a muted
    or empty Session track, or a MIDI track without devices. External routing and
    Arrangement audio may explain these states. Returns machine-readable
    findings so you can decide what to fix. Reads the session; changes nothing."""
    snapshot = get_ableton_connection().send_command("get_session_snapshot")
    findings = mix_findings(snapshot)
    return {
        "evidence": "heuristic",
        "limitations": "Session/fader state only. Does not measure audio, clipping, LUFS or true peak.",
        "track_count": snapshot.get("track_count"),
        "issue_count": len(findings),
        "issues": findings,
    }


_LAST_SNAPSHOT = {"data": None}
_SNAPSHOT_LOCK = threading.Lock()


def _snapshot_delta(prev, cur):
    """Diff two get_session_snapshot results into a compact change dict."""
    changes = {}
    for k in ("tempo", "time_signature", "is_playing"):
        if prev.get(k) != cur.get(k):
            changes[k] = {"from": prev.get(k), "to": cur.get(k)}
    before, after = prev.get("tracks", []), cur.get("tracks", [])
    if (
        not prev.get("snapshot_scope")
        or prev.get("snapshot_scope") != cur.get("snapshot_scope")
        or any("id" not in t for t in before + after)
        or len({t["id"] for t in before}) != len(before)
        or len({t["id"] for t in after}) != len(after)
    ):
        changes["track_identity_available"] = False
        changes["warning"] = (
            "Track identity cannot be compared across these snapshots. "
            "No track additions, removals or renames inferred; inspect tracks directly."
        )
        return changes
    pa = {t["id"]: t for t in before}
    cb = {t["id"]: t for t in after}
    changes["tracks_added"] = [cb[i]["name"] for i in cb if i not in pa]
    changes["tracks_removed"] = [pa[i]["name"] for i in pa if i not in cb]
    changes["tracks_renamed"] = [
        {"id": i, "from": pa[i]["name"], "to": cb[i]["name"]}
        for i in cb
        if i in pa and pa[i]["name"] != cb[i]["name"]
    ]
    modified = []
    for identity in cb:
        if identity not in pa:
            continue
        a, b = pa[identity], cb[identity]
        delta = {
            k: {"from": a.get(k), "to": b.get(k)}
            for k in ("index", "muted", "soloed", "armed", "volume", "clips", "devices")
            if a.get(k) != b.get(k)
        }
        if delta:
            modified.append({"id": identity, "track": b["name"], **delta})
    changes["tracks_modified"] = modified
    return changes


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def session_diff() -> dict:
    """Report what changed in the set since the LAST time you called this tool:
    tempo/time-signature/play-state, tracks added, removed, renamed or reordered, and per-track volume,
    mute, solo, arm, clip-count, and device changes. The first call records a
    baseline (no diff). Track IDs are scoped to the running bridge; older bridges
    cannot identify renames reliably. Call it before and after an edit to verify it took effect."""
    with _SNAPSHOT_LOCK:
        cur = get_ableton_connection().send_command("get_session_snapshot")
        prev = _LAST_SNAPSHOT["data"]
        _LAST_SNAPSHOT["data"] = cur
    if prev is None or prev.get("snapshot_scope") != cur.get("snapshot_scope"):
        return {"baseline": True, "track_count": cur.get("track_count")}
    return {"changes": _snapshot_delta(prev, cur)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def describe_capabilities() -> dict:
    """A map of all available groups, the enabled groups, and conventions to
    follow, so you can orient before your first call. Set
    ABLETON_TOOLSETS to load only some groups. For the current live state, call
    get_session_snapshot; for mix problems, analyze_mix."""
    return {
        "groups": GROUP_DESCRIPTIONS,
        "enabled_groups": [group for group in ENABLED_GROUPS if group in GROUP_DESCRIPTIONS],
        "conventions": _CONVENTIONS,
        "audio_acceleration": "native" if NATIVE_AUDIO else "python",
    }
