"""Import selected tool modules so their @mcp.tool decorators register.

Set ABLETON_TOOLSETS to a comma-separated subset of groups to load fewer tools
(helps LLMs pick the right tool when the full surface is more than a task needs),
e.g. ABLETON_TOOLSETS="session,tracks,clips,generators". Default loads everything.
Groups (a domain may span several modules; see _groups.py): session, tracks, clips,
devices, browser, arrangement, generators, audio, analysis, offline, recipes.
The `offline` group parses saved .als/.adg files and needs no running Live.
"""

import importlib
import os

from ._groups import GROUP_MODULES

_requested = os.environ.get("ABLETON_TOOLSETS", "").strip()
if _requested and _requested.lower() != "all":
    _named = [g.strip() for g in _requested.split(",") if g.strip()]
    _unknown = [g for g in _named if g not in GROUP_MODULES]
    if _unknown:
        _valid = [g for g in GROUP_MODULES if g != "prompts"]
        raise ValueError(f"Unknown ABLETON_TOOLSETS: {_unknown}. Valid groups: {_valid}")
    _selected = [g for g in _named if g in GROUP_MODULES]
    if "prompts" not in _selected:
        _selected.append("prompts")
else:
    _selected = list(GROUP_MODULES)

# Flatten selected groups to their modules, de-duplicated, preserving order.
_modules = []
for _g in _selected:
    for _mod in GROUP_MODULES[_g]:
        if _mod not in _modules:
            _modules.append(_mod)

for _m in _modules:
    importlib.import_module(f".{_m}", __name__)
