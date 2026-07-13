"""The FastMCP app registers the full tool surface with unique names."""

import asyncio

GLAMA_REVIEWED_TOOLS = {
    "clear_automation",
    "create_audio_track",
    "create_locator",
    "delete_clip",
    "duplicate_scene",
    "duplicate_track",
    "fire_clip",
    "fire_scene",
    "generate_chord_progression",
    "get_clip_notes",
    "get_session_info",
    "get_track_info",
    "load_device_to_return",
    "load_drum_kit",
    "set_clip_color",
    "set_clip_loop",
    "set_clip_name",
    "set_crossfade_assign",
    "set_device_enabled",
    "set_loop",
    "set_master_device_parameter",
    "set_record_mode",
    "set_return_device_parameter",
    "set_scene_name",
    "set_time_signature",
    "set_track_mute",
    "set_track_pan",
    "set_track_solo",
    "undo",
}


def test_tool_registration():
    from ableton_live_mcp import tools as _tools  # noqa: F401  (registers the tools)
    from ableton_live_mcp.app import mcp

    tools = asyncio.run(mcp.list_tools())
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "duplicate tool names"
    assert len(names) == 154, f"expected 154 tools, README/manifest say 154, got {len(names)}"
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
        "als_details",
        "preview_browser_item",
    ):
        assert expected in names


def test_version_consistency():
    """The hand-maintained version strings must agree everywhere."""
    import json
    import pathlib
    import re

    root = pathlib.Path(__file__).parent.parent
    from ableton_live_mcp import __version__

    pyproject = (root / "pyproject.toml").read_text()
    assert re.search(rf'^version = "{re.escape(__version__)}"$', pyproject, re.M), (
        f"pyproject.toml version != {__version__}"
    )
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["version"] == __version__
    server = json.loads((root / "server.json").read_text())
    assert server["version"] == __version__
    assert server["packages"][0]["version"] == __version__
    assert len(server["description"]) <= 100, "Official MCP Registry limit"
    assert server["websiteUrl"] == "https://abletonmcp.com"


def test_glama_reviewed_tools_have_documented_input_schemas():
    """Keep the parameter-description gap fixed for every tool Glama rated B/C."""
    from ableton_live_mcp import tools as _tools  # noqa: F401
    from ableton_live_mcp.app import mcp

    by_name = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert GLAMA_REVIEWED_TOOLS <= by_name.keys()

    missing = []
    for name in sorted(GLAMA_REVIEWED_TOOLS):
        for parameter, schema in by_name[name].inputSchema.get("properties", {}).items():
            if not schema.get("description"):
                missing.append(f"{name}.{parameter}")
    assert not missing, f"missing input-schema descriptions: {', '.join(missing)}"


def test_glama_metadata_claims_repository_owner():
    import json
    import pathlib

    metadata = json.loads((pathlib.Path(__file__).parent.parent / "glama.json").read_text())
    assert metadata == {
        "$schema": "https://glama.ai/mcp/schemas/server.json",
        "maintainers": ["wstierhout"],
    }
