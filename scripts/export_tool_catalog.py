"""Export the full tool contract as deterministic JSON, without connecting to Live."""

import argparse
import ast
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.pop("ABLETON_TOOLSETS", None)

from ableton_live_mcp import __version__, tools  # noqa: E402, F401
from ableton_live_mcp.app import mcp  # noqa: E402
from ableton_live_mcp.tools._groups import GROUP_MODULES  # noqa: E402


def catalogue():
    groups = {}
    for group, modules in GROUP_MODULES.items():
        for module in modules:
            tree = ast.parse((ROOT / "ableton_live_mcp/tools" / f"{module}.py").read_text())
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    groups[node.name] = group
    entries = []
    for tool in sorted(asyncio.run(mcp.list_tools()), key=lambda item: item.name):
        entries.append(
            {
                "name": tool.name,
                "group": groups[tool.name],
                "description": tool.description,
                "inputSchema": tool.inputSchema,
                "annotations": tool.annotations.model_dump(exclude_none=True)
                if tool.annotations
                else {},
            }
        )
    return {"version": __version__, "toolCount": len(entries), "tools": entries}


def check_release():
    """Check version drift before publishing, independently of functional tests."""
    manifest = json.loads((ROOT / "manifest.json").read_text())
    registry = json.loads((ROOT / "server.json").read_text())
    project = re.search(
        r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M
    ).group(1)
    bridge = ast.parse((ROOT / "ableton_live_mcp/remote_script/__init__.py").read_text())
    bridge_version = next(
        ast.literal_eval(node.value)
        for node in bridge.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "BRIDGE_VERSION" for t in node.targets)
    )
    versions = [
        project,
        manifest["version"],
        registry["version"],
        bridge_version,
        *(package["version"] for package in registry["packages"]),
    ]
    plugin = ROOT / "plugins/ableton-live"
    if plugin.is_dir():
        versions.append(json.loads((plugin / ".codex-plugin/plugin.json").read_text())["version"])
        config = json.loads((plugin / ".mcp.json").read_text())
        assert config["mcpServers"]["ableton"]["args"] == [f"mcp-server-ableton-live@{__version__}"]
    assert all(version == __version__ for version in versions), f"Version drift: {versions}"
    assert len(registry["description"]) <= 100, "MCP Registry description limit"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="Check release version consistency")
    args = parser.parse_args()
    if args.check:
        check_release()
        print(f"Release versions agree: {__version__}")
    payload = json.dumps(catalogue(), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    elif not args.check:
        print(payload, end="")
