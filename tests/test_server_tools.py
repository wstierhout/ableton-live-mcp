"""The FastMCP app registers the full tool surface with unique names."""

import asyncio


def test_tool_registration():
    from ableton_live_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "duplicate tool names"
    assert len(names) == 104, f"expected 104 tools, README/CHANGELOG say 104, got {len(names)}"
    for expected in (
        "get_session_info",
        "set_track_volume",
        "write_automation",
        "quantize_clip",
        "load_device_to_master",
    ):
        assert expected in names
