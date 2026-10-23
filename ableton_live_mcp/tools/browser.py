"""Ableton browser navigation and loading."""

from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import AbletonCommandError, get_ableton_connection
from ._util import BrowserItemUri, BrowserPath, TrackIndex, params


def _browser_result(ableton, command, arguments):
    result = ableton.send_command(command, arguments)
    if result.get("error"):  # Older bridges returned lookup failures as successful payloads.
        raise AbletonCommandError(str(result["error"]), {"may_have_applied": False})
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def get_browser_tree(category_type: str = "all") -> dict:
    """List supported browser roots with paths; this is a shallow category listing.
    Use get_browser_items_at_path to read children, or search_browser for names.
    category_type: "all" or a supported category such as instruments, sounds,
    drums, audio_effects, midi_effects, plugins, max_for_live, user_library, packs,
    samples, clips, grooves or current_project. Availability depends on Live.
    """
    result = _browser_result(
        get_ableton_connection(), "get_browser_tree", {"category_type": category_type}
    )
    result["category_count"] = len(result.get("categories", []))
    if category_type != "all" and not result["category_count"] and not result.get("partial"):
        raise AbletonCommandError(f"Browser category '{category_type}' is unavailable")
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def get_browser_items_at_path(path: str) -> dict:
    """List immediate entries at a category/folder path from get_browser_tree.
    Listings are bounded; inspect partial/limit_reasons before concluding no match.
    """
    return _browser_result(get_ableton_connection(), "get_browser_items_at_path", {"path": path})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def load_drum_kit(
    track_index: TrackIndex,
    rack_uri: BrowserItemUri,
    kit_path: BrowserPath,
) -> str:
    """Resolve the first loadable kit at a path, then load a rack and that kit.
    A missing kit fails before edits. If loading fails after the rack was added,
    the error reports completed edits; they are not rolled back. Prefer
    load_instrument_or_effect when one known browser URI is sufficient.
    """
    ableton = get_ableton_connection()
    result = _browser_result(ableton, "get_browser_items_at_path", {"path": kit_path})
    kit = next(
        (
            item
            for item in result.get("items", [])
            if item.get("is_loadable") and isinstance(item.get("uri"), str) and item["uri"]
        ),
        None,
    )
    if kit is None:
        reason = (
            "Browser listing is incomplete" if result.get("partial") else "No loadable kit found"
        )
        raise AbletonCommandError(
            f"{reason} at '{kit_path}'; no rack was loaded",
            {"phase": "preflight", "may_have_applied": False, "completed_count": 0},
        )
    completed = []
    try:
        for uri in (rack_uri, kit["uri"]):
            loaded = ableton.send_command(
                "load_browser_item", {"track_index": track_index, "item_uri": uri}
            )
            completed.append({"uri": uri, "result": loaded})
    except Exception as exc:
        raise AbletonCommandError(
            f"Drum-kit loading failed after {len(completed)} completed loads: {exc}. "
            "Completed edits were not rolled back.",
            {
                **getattr(exc, "details", {}),
                "completed_count": len(completed),
                "completed_results": completed,
                "may_have_applied": bool(completed)
                or getattr(exc, "details", {}).get("may_have_applied", True),
            },
        ) from exc
    return f"Loaded drum rack and kit '{kit.get('name', kit['uri'])}' on track {track_index}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def search_browser(query: str, category: str | None = None, max_results: int = 25) -> dict:
    """Search the Ableton browser by display name (case-insensitive substring).
    Returns loadable items with their URIs for load_instrument_or_effect /
    load_device_to_master / load_device_to_return.

    category: optionally limit to a supported root from get_browser_tree
    (default: visit all roots fairly over multiple Live ticks). Empty query lists
    loadable items. Requests over 1,000 results are capped and reported explicitly.
    Check partial/limit_reasons before treating an empty result as no match.
    Prefer this over walking get_browser_items_at_path when you know a name.
    """
    return get_ableton_connection().send_command(
        "search_browser", params(query=query, max_results=max_results, category=category)
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def preview_browser_item(item_uri: str) -> dict:
    """Audition a browser item (a sample, instrument, or preset) through Live's
    preview tab WITHOUT loading it onto a track. `item_uri` comes from
    search_browser or get_browser_items_at_path. Use stop_browser_preview to stop."""
    return get_ableton_connection().send_command("preview_browser_item", {"item_uri": item_uri})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def stop_browser_preview() -> dict:
    """Stop the browser preview started by preview_browser_item."""
    return get_ableton_connection().send_command("stop_browser_preview")
