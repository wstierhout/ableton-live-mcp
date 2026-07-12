"""Import selected tool modules so their @mcp.tool decorators register.

Set ABLETON_TOOLSETS to a comma-separated subset to load fewer tools (helps LLMs
pick the right tool when the full tool surface is more than a task needs),
e.g. ABLETON_TOOLSETS="session,tracks,clips,generators". Default loads all.
Available: session, tracks, clips, devices, browser, arrangement, generators,
offline, prompts. The `offline` group parses saved .als files and needs no
running Live.
"""

import importlib
import os

_ALL = [
    "session",
    "tracks",
    "clips",
    "devices",
    "browser",
    "arrangement",
    "generators",
    "generators_advanced",
    "motif",
    "offline",
    "offline_racks",
    "analysis",
    "keys",
    "device_kb",
    "audio",
    "recipes",
    "prompts",
]

_requested = os.environ.get("ABLETON_TOOLSETS", "").strip()
if _requested and _requested.lower() != "all":
    _named = [m.strip() for m in _requested.split(",") if m.strip()]
    _unknown = [m for m in _named if m not in _ALL]
    if _unknown:
        raise ValueError(
            f"Unknown ABLETON_TOOLSETS: {_unknown}. Valid groups: {[g for g in _ALL if g != 'prompts']}"
        )
    _selected = [m for m in _named if m in _ALL]
    if "prompts" not in _selected:
        _selected.append("prompts")
else:
    _selected = _ALL

for _m in _selected:
    importlib.import_module(f".{_m}", __name__)
