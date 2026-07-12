"""Ableton browser navigation and loading."""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import AbletonCommandError, get_ableton_connection, logger
from ._util import BrowserItemUri, BrowserPath, TrackIndex, params


def _translate_browser_error(e: Exception, doing: str) -> Exception:
    """Map the Remote Script's browser failures to actionable messages."""
    error_msg = str(e)
    if "Browser is not available" in error_msg:
        logger.error(f"Browser is not available in Ableton: {error_msg}")
        return Exception(
            "Error: The Ableton browser is not available. "
            "Make sure Ableton Live is fully loaded and try again."
        )
    if "Could not access Live application" in error_msg:
        logger.error(f"Could not access Live application: {error_msg}")
        return Exception(
            "Error: Could not access the Ableton Live application. "
            "Make sure Ableton Live is running and the Remote Script is loaded."
        )
    logger.error(f"Error {doing}: {error_msg}")
    return Exception(f"Error {doing}: {error_msg}")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def get_browser_tree(ctx: Context, category_type: str = "all") -> str:
    """List the browser's top-level categories and their immediate entries.
    NOTE: returns a shallow listing (no deep hierarchy) - drill down with
    get_browser_items_at_path("instruments"), ...("sounds/Piano & Keys"), etc.,
    or skip straight to search_browser when you know a name.
    category_type: "all", "instruments", "sounds", "drums", "audio_effects", "midi_effects".
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_tree", {"category_type": category_type})

        if "available_categories" in result and len(result.get("categories", [])) == 0:
            available_cats = result.get("available_categories", [])
            raise ValueError(
                f"No categories found for '{category_type}'. "
                f"Available browser categories: {', '.join(available_cats)}"
            )

        total_folders = result.get("total_folders", 0)
        formatted_output = (
            f"Browser tree for '{category_type}' (showing {total_folders} folders):\n\n"
        )

        def format_tree(item, indent=0):
            output = ""
            if item:
                prefix = "  " * indent
                name = item.get("name", "Unknown")
                path = item.get("path", "")
                has_more = item.get("has_more", False)

                output += f"{prefix}• {name}"
                if path:
                    output += f" (path: {path})"
                if has_more:
                    output += " [...]"
                output += "\n"

                for child in item.get("children", []):
                    output += format_tree(child, indent + 1)
            return output

        for category in result.get("categories", []):
            formatted_output += format_tree(category)
            formatted_output += "\n"

        return formatted_output
    except AbletonCommandError as e:
        raise _translate_browser_error(e, "getting browser tree") from e


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def get_browser_items_at_path(ctx: Context, path: str) -> str:
    """
    Get browser items at a specific path in Ableton's browser.

    Parameters:
    - path: Path in the format "category/folder/subfolder"
            where category is one of the available browser categories in Ableton
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_items_at_path", {"path": path})

        # The Remote Script reports lookup failures ("Path part 'x' not found",
        # "Unknown or unavailable category", ...) as a success payload with an
        # "error" key - surface every one of them as a real error.
        if "error" in result:
            error = result["error"]
            available_cats = result.get("available_categories")
            if available_cats:
                raise ValueError(
                    f"Error: {error}. Available browser categories: {', '.join(available_cats)}"
                )
            raise ValueError(f"Error: {error}. Check the path with get_browser_tree.")

        return json.dumps(result, indent=2)
    except AbletonCommandError as e:
        raise _translate_browser_error(e, "getting browser items at path") from e


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def load_drum_kit(
    ctx: Context,
    track_index: TrackIndex,
    rack_uri: BrowserItemUri,
    kit_path: BrowserPath,
) -> str:
    """Load a Drum Rack, then the first loadable kit at a browser path.

    Use search_browser to obtain the rack URI and get_browser_items_at_path to
    verify the kit path. This is a two-step mutation: it appends the rack first,
    then loads the first loadable item found at `kit_path`. If path lookup fails,
    the new empty rack remains on the track; repeated calls add more racks. Use
    load_instrument_or_effect when one known browser URI is sufficient.
    """
    ableton = get_ableton_connection()

    # Step 1: Load the drum rack (the Remote Script raises on failure).
    ableton.send_command("load_browser_item", {"track_index": track_index, "item_uri": rack_uri})

    # Step 2: Get the drum kit items at the specified path
    kit_result = ableton.send_command("get_browser_items_at_path", {"path": kit_path})

    if "error" in kit_result:
        return f"Loaded drum rack but failed to find drum kit: {kit_result.get('error')}"

    # Step 3: Find a loadable drum kit
    kit_items = kit_result.get("items", [])
    loadable_kits = [item for item in kit_items if item.get("is_loadable", False)]

    if not loadable_kits:
        return f"Loaded drum rack but no loadable drum kits found at '{kit_path}'"

    # Step 4: Load the first loadable kit
    kit_uri = loadable_kits[0].get("uri")
    ableton.send_command("load_browser_item", {"track_index": track_index, "item_uri": kit_uri})

    return f"Loaded drum rack and kit '{loadable_kits[0].get('name')}' on track {track_index}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def search_browser(
    ctx: Context, query: str, category: str | None = None, max_results: int = 25
) -> str:
    """Search the Ableton browser by display name (case-insensitive substring).
    Returns loadable items with their URIs for load_instrument_or_effect /
    load_device_to_master / load_device_to_return.

    category: optionally limit to one of "instruments", "sounds", "drums",
    "audio_effects", "midi_effects", "packs" (default: search all of them).
    Prefer this over walking get_browser_items_at_path when you know a name.
    """
    result = get_ableton_connection().send_command(
        "search_browser", params(query=query, max_results=max_results, category=category)
    )
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def preview_browser_item(ctx: Context, item_uri: str) -> str:
    """Audition a browser item (a sample, instrument, or preset) through Live's
    preview tab WITHOUT loading it onto a track. `item_uri` comes from
    search_browser or get_browser_items_at_path. Use stop_browser_preview to stop."""
    r = get_ableton_connection().send_command("preview_browser_item", {"item_uri": item_uri})
    return f"Previewing: {r.get('previewing')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def stop_browser_preview(ctx: Context) -> str:
    """Stop the browser preview started by preview_browser_item."""
    get_ableton_connection().send_command("stop_browser_preview")
    return "Preview stopped"
