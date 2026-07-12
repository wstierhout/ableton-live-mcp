"""The FastMCP app registers the full tool surface with unique names."""

import asyncio


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
