"""Core public tool contracts; release metadata is checked by the catalog exporter."""

import asyncio
import json
import os
import subprocess
import sys

import pytest


def test_core_tools_publish_callable_parameter_schemas():
    from ableton_live_mcp import tools as _tools  # noqa: F401
    from ableton_live_mcp.app import mcp

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    contracts = {
        "set_track_volume": {"track_index": "integer", "volume": "number"},
        "add_notes_to_clip": {"track_index": "integer", "clip_index": "integer", "notes": "array"},
        "record_section": {"start_beat": "number", "end_beat": "number"},
        "batch_commands": {"commands": "array"},
    }
    for name, fields in contracts.items():
        schema = tools[name].inputSchema
        assert set(fields) <= set(schema["required"])
        assert "ctx" not in schema["properties"]
        for field, kind in fields.items():
            assert schema["properties"][field]["type"] == kind


@pytest.mark.parametrize("selection", ["recipes", "recipes,analysis"])
def test_selected_groups_exclude_dependency_tools_but_keep_helpers(selection):
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, json
from ableton_live_mcp.app import mcp
from ableton_live_mcp.tools import recipes
async def inspect():
    tools = [tool.name for tool in await mcp.list_tools()]
    recipe_list = await mcp.call_tool("list_recipes", {})
    capabilities = (await mcp.call_tool("describe_capabilities", {})).structuredContent \
        if "describe_capabilities" in tools else None
    print(json.dumps({"tools": tools, "recipes": recipe_list.structuredContent,
                      "notes": recipes.ga.drum_groove(bars=1), "capabilities": capabilities}))
asyncio.run(inspect())
""",
        ],
        env={**os.environ, "ABLETON_TOOLSETS": selection},
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(process.stdout)
    expected = {"list_recipes", "apply_recipe"}
    if "analysis" in selection:
        expected |= {
            "analyze_mix",
            "session_diff",
            "describe_capabilities",
            "detect_clip_key",
            "detect_session_key",
            "detect_track_key",
            "als_detect_key",
            "analyze_audio_file",
        }
    assert set(result["tools"]) == expected
    if "analysis" in selection:
        assert result["capabilities"]["data"]["enabled_groups"] == ["recipes", "analysis"]
        assert "generators" in result["capabilities"]["data"]["groups"]
    assert "lofi_beat" in result["recipes"]["data"]
    assert result["notes"]


def test_unknown_tool_selection_fails_at_startup():
    process = subprocess.run(
        [sys.executable, "-c", "import ableton_live_mcp.tools"],
        env={**os.environ, "ABLETON_TOOLSETS": "recipez"},
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert "Unknown ABLETON_TOOLSETS: ['recipez']" in process.stderr
