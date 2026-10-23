"""FastMCP application instance and server lifecycle."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio.to_thread
from mcp.server.fastmcp import FastMCP

from .connection import disconnect_ableton, get_ableton_connection
from .execution import WorkerMCP, finish_cleanup

logger = logging.getLogger("AbletonMCPServer")


async def _connect_on_startup() -> None:
    """Best-effort connection warm-up; every tool call lazily connects anyway."""
    try:
        await anyio.to_thread.run_sync(lambda: get_ableton_connection().diagnostics())
        logger.info("Successfully connected to Ableton on startup")
    except Exception as e:
        logger.warning(f"Could not connect to Ableton on startup: {str(e)}")
        logger.warning("Make sure the Ableton Remote Script is running")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Warm up the Ableton connection on startup and clean up on shutdown."""
    logger.info("AbletonMCP server starting up")
    # Fire-and-forget: connect retries take up to ~17 s when Live is closed and
    # must not delay the MCP handshake.
    warmup = asyncio.ensure_future(_connect_on_startup())
    try:
        yield {}
    finally:

        async def shutdown():
            await server.drain_workers()
            # A cancelled to_thread await cannot stop the underlying connector.
            # Join it before disconnecting so it cannot reconnect after shutdown.
            await asyncio.gather(warmup, return_exceptions=True)
            await anyio.to_thread.run_sync(disconnect_ableton)

        await finish_cleanup(shutdown())
        logger.info("AbletonMCP server shut down")


INSTRUCTIONS = """Control Ableton Live locally. Read session/target state before editing and verify
changes afterwards. Replacing notes, samples or arrangement regions can destroy
musical content; preserve the original and confirm targets. A timeout is not proof
an edit failed: inspect state before retrying. Offline .als tools work with Live
closed. Mix heuristics are not measured audio; decoded sample peak/RMS is not LUFS.

Conventions:

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
- Start, stop and seek tools report requested and observed states after a later Live tick.
  Only confirmed=true means the requested state was observed.
- Use batch_commands for multi-step edits: one round-trip, one undo step.
- If every command suddenly times out, a modal dialog is open in Live (e.g. the
  trial nag) - it freezes the Remote Script until dismissed. Ask the user to
  press Enter in Live, then retry.
- undo/redo are available; prefer batch_commands so one undo reverts a whole edit.
"""

mcp = WorkerMCP("Ableton Live MCP Server", lifespan=server_lifespan, instructions=INSTRUCTIONS)
