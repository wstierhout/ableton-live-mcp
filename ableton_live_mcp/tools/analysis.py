"""Server-side analysis and orientation tools (no Live API risk).

`analyze_mix` reasons over a live session snapshot to flag likely mix problems;
`describe_capabilities` gives an agent a high-level map of the toolset before its
first call. Both are pure logic over data the other tools already return.
"""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection

# Compact map of what each toolset group covers, for agent orientation. Kept in
# sync with the tool modules by hand; it is a summary, not a generated index.
_GROUPS = {
    "session": "transport, tempo, tap tempo, groove/swing, scenes, locators, record modes, song scale, Ableton Link, one-call snapshot",
    "tracks": "create/delete MIDI/audio/return tracks, delete devices, take lanes, volume/pan/mute/solo/arm/sends, routing, meters, crossfader",
    "clips": "create clips, write/edit MIDI notes (probability), quantize with strength, groove, loop, warp, pitch/gain",
    "devices": "browse/search and load devices onto any track incl Master and Returns, read/set any param, rack macro variations, Simpler slicing, per-pad drum control",
    "arrangement": "place/read/delete arrangement clips, write clip automation",
    "generators": "drum patterns, euclidean rhythms, chord progressions, jazz voicings, voice-leading melodies, walking bass, genre progressions, humanize, session setup",
    "offline": "parse, diff, and lint saved .als files with Live closed",
    "analysis": "scan the mix for problems and describe the toolset",
    "recipes": "scaffold a genre starter (lofi, house) in one call",
}

_CONVENTIONS = [
    "Indices are 0-based; times and lengths are in beats.",
    "Volume is Live's 0.0 to 1.0 fader range where 0.85 is 0 dB.",
    "add_notes_to_clip replaces a clip's notes; use edit_notes for incremental changes.",
    "Device parameters use each parameter's own range; read get_device_parameters first.",
    "Transport replies report the pre-command state; confirm with get_session_info.",
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
                "message": f"{len(loud)} tracks are pushed above 0 dB (fader >= 0.9), leaving little headroom: {loud}",
            }
        )

    if not any((not t.get("muted")) and (t.get("clips") or 0) > 0 for t in tracks):
        findings.append(
            {
                "code": "nothing_playing",
                "severity": "warn",
                "message": "No unmuted track has any Session clips, so nothing will play in Session view.",
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
                    "message": f"MIDI track '{name}' has no instrument, so it makes no sound.",
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
def analyze_mix(ctx: Context) -> str:
    """Scan the current live set for likely mix problems: several tracks at or
    above 0 dB (no headroom), a muted or empty track, a MIDI track with no
    instrument, or nothing that will actually play. Returns machine-readable
    findings so you can decide what to fix. Reads the session; changes nothing."""
    snapshot = get_ableton_connection().send_command("get_session_snapshot")
    findings = mix_findings(snapshot)
    return json.dumps(
        {
            "track_count": snapshot.get("track_count"),
            "issue_count": len(findings),
            "issues": findings,
        },
        indent=2,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def describe_capabilities(ctx: Context) -> str:
    """A high-level map of this server: the tool groups and what each covers, plus
    the conventions to follow, so you can orient before your first call. Set
    ABLETON_TOOLSETS to load only some groups. For the current live state, call
    get_session_snapshot; for mix problems, analyze_mix."""
    return json.dumps({"groups": _GROUPS, "conventions": _CONVENTIONS}, indent=2)
