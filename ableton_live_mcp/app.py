"""FastMCP application instance and server lifecycle."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from .connection import disconnect_ableton, get_ableton_connection

logger = logging.getLogger("AbletonMCPServer")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Connect to Ableton on startup (best effort) and clean up on shutdown."""
    try:
        logger.info("AbletonMCP server starting up")
        try:
            get_ableton_connection()
            logger.info("Successfully connected to Ableton on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Ableton on startup: {str(e)}")
            logger.warning("Make sure the Ableton Remote Script is running")
        yield {}
    finally:
        disconnect_ableton()
        logger.info("AbletonMCP server shut down")


INSTRUCTIONS = """Control Ableton Live via a Remote Script socket. Conventions:

- ALL indices are 0-based: track_index, clip_index (session slot), scene_index,
  return_index (0 = Return A), device_index (position in the track's chain).
- Times, lengths, and positions are in BEATS (quarter notes), floats allowed.
- Volume faders are Live's normalized 0.0-1.0 range where 0.85 = 0 dB unity.
  Device parameters use each parameter's native range - call get_device_parameters
  first and read min/max/display before setting values.
- MIDI notes: pitch 0-127 (60 = C3 in Live's naming), velocity 1-127,
  start_time/duration in beats relative to clip start.
- Typical workflow: create_midi_track -> load_instrument_or_effect (or
  search_browser + load) -> create_clip -> add_notes_to_clip -> fire_clip /
  duplicate_to_arrangement. Check get_session_info / get_track_info first.
- add_notes_to_clip REPLACES the clip's entire note content (use edit_notes for
  incremental changes). duplicate_to_arrangement OVERWRITES whatever overlaps the
  destination range. delete_track / delete_arrangement_clip shift later indices.
- Transport tools report the PRE-command state (start_playback may answer
  "playing: false"); confirm with get_session_info.
- Use batch_commands for multi-step edits: one round-trip, one undo step.
- If every command suddenly times out, a modal dialog is open in Live (e.g. the
  trial nag) - it freezes the Remote Script until dismissed. Ask the user to
  press Enter in Live, then retry.
- undo/redo are available; prefer batch_commands so one undo reverts a whole edit.
"""

mcp = FastMCP("AbletonMCP", lifespan=server_lifespan, instructions=INSTRUCTIONS)
