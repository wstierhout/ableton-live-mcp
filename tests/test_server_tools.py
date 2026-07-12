"""The FastMCP app registers the full tool surface with unique names."""

import asyncio


def test_tool_registration():
    from ableton_live_mcp import tools as _tools  # noqa: F401  (registers the tools)
    from ableton_live_mcp.app import mcp

    tools = asyncio.run(mcp.list_tools())
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "duplicate tool names"
    assert len(names) == 149, f"expected 149 tools, README/CHANGELOG say 149, got {len(names)}"
    for expected in (
        "get_session_info",
        "set_track_volume",
        "write_automation",
        "quantize_clip",
        "load_device_to_master",
        "als_diff",
        "generate_walking_bass",
        "tap_tempo",
        "get_session_snapshot",
        "apply_recipe",
        "detect_session_key",
        "record_section",
        "adg_analyze",
    ):
        assert expected in names
