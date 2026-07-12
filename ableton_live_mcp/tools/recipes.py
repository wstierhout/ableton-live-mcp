"""Recipe system: one call scaffolds a genre-flavored starting point.

A recipe sets the tempo, creates and names tracks, tries to load fitting
instruments (best effort, since it depends on the user's library), and writes
generated parts. Recipes only orchestrate existing tools and the pure generators,
so they add no new Live-API surface.
"""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import AbletonCommandError, get_ableton_connection, logger
from . import generators_advanced as ga
from .generators import _write_clip


def _new_track(conn, name):
    idx = conn.send_command("create_midi_track", {"index": -1})["index"]
    conn.send_command("set_track_name", {"track_index": idx, "name": name})
    return idx


def _try_load(conn, track_index, query):
    """Best-effort: search the browser and load the first fitting item. Search/
    load failures are swallowed so the recipe still completes (the track is just
    silent until an instrument is added) - but a dead connection is not, since
    every later step would fail anyway. Returns the loaded item's name or None."""
    try:
        result = conn.send_command("search_browser", {"query": query, "max_results": 5})
        matches = result.get("matches") if isinstance(result, dict) else None
        loadable = [m for m in (matches or []) if isinstance(m, dict) and m.get("uri")]
        pick = next((m for m in loadable if m.get("is_device")), None) or (
            loadable[0] if loadable else None
        )
        if pick:
            conn.send_command(
                "load_browser_item", {"track_index": track_index, "item_uri": pick["uri"]}
            )
            return pick.get("name")
    except AbletonCommandError as e:
        logger.debug(f"Recipe instrument load skipped ({query!r}): {e}")
    return None


def _build(
    conn, tempo, key, scale, bars, *, genre, drum_style, bass_query, chord_query, chord_style
):
    if bars < 1:
        raise ValueError("bars must be at least 1")
    conn.send_command("set_tempo", {"tempo": tempo})
    chords = ga.progression_for_genre(genre, key, scale, bars)
    length = bars * 4.0
    drum_bars = min(bars, 2)
    result = {"genre": genre, "tempo": tempo, "key": key, "progression": chords, "tracks": []}

    drums = _new_track(conn, "Drums")
    kit = _try_load(conn, drums, "drum")
    _write_clip(drums, 0, drum_bars * 4.0, ga.drum_groove(bars=drum_bars, style=drum_style))
    result["tracks"].append({"name": "Drums", "index": drums, "instrument": kit})

    bass = _new_track(conn, "Bass")
    binst = _try_load(conn, bass, bass_query)
    _write_clip(bass, 0, length, ga.walking_bass(chords, key=key, scale=scale))
    result["tracks"].append({"name": "Bass", "index": bass, "instrument": binst})

    chord_track = _new_track(conn, "Chords")
    cinst = _try_load(conn, chord_track, chord_query)
    _write_clip(chord_track, 0, length, ga.voice_progression(chords, style=chord_style))
    result["tracks"].append({"name": "Chords", "index": chord_track, "instrument": cinst})
    return result


RECIPES = {
    "lofi_beat": {
        "description": "Dusty lofi hip-hop starter: swung drums, walking upright bass, rootless Rhodes chords in a jazzy minor key.",
        "defaults": {"tempo": 82, "key": "F", "scale": "minor", "bars": 4},
        "args": {
            "genre": "lofi",
            "drum_style": "lofi",
            "bass_query": "bass",
            "chord_query": "electric piano",
            "chord_style": "rootless",
        },
    },
    "house_groove": {
        "description": "Four-on-the-floor house starter: driving drums, offbeat bass, shell-voiced piano stabs.",
        "defaults": {"tempo": 124, "key": "A", "scale": "minor", "bars": 4},
        "args": {
            "genre": "house",
            "drum_style": "house",
            "bass_query": "bass",
            "chord_query": "piano",
            "chord_style": "shell",
        },
    },
}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_recipes(ctx: Context) -> str:
    """List the built-in composition recipes (name, description, and default
    tempo/key/scale/bars), for use with apply_recipe."""
    return json.dumps(
        {
            n: {"description": r["description"], "defaults": r["defaults"]}
            for n, r in RECIPES.items()
        },
        indent=2,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def apply_recipe(
    ctx: Context,
    name: str,
    tempo: float | None = None,
    key: str | None = None,
    scale: str | None = None,
    bars: int | None = None,
) -> str:
    """Scaffold a genre starter in one call: set the tempo, add Drums, Bass, and
    Chords MIDI tracks, try to load fitting instruments, and write generated
    parts. `name` is one from list_recipes (e.g. "lofi_beat", "house_groove").
    tempo/key/scale/bars override the recipe defaults. Adds new tracks; it does
    not overwrite existing ones."""
    recipe = RECIPES.get(name)
    if recipe is None:
        raise ValueError(f"Unknown recipe '{name}'. Options: {sorted(RECIPES)}")
    d = recipe["defaults"]
    result = _build(
        get_ableton_connection(),
        tempo if tempo is not None else d["tempo"],
        key or d["key"],
        scale or d["scale"],
        bars if bars is not None else d["bars"],
        **recipe["args"],
    )
    return json.dumps(result, indent=2)
