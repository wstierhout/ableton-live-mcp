"""Import selected tool modules so their @mcp.tool decorators register.

Set ABLETON_TOOLSETS to a comma-separated subset of groups to load fewer tools
(helps LLMs pick the right tool when the full surface is more than a task needs),
e.g. ABLETON_TOOLSETS="session,tracks,clips,generators". Default loads everything.
Groups (a domain may span several modules; see _groups.py): session, tracks, clips,
devices, browser, arrangement, generators, audio, analysis, offline, recipes.
The `offline` group parses saved .als/.adg files and needs no running Live.
"""

import importlib

from ..app import mcp
from ._groups import ENABLED_MODULES

# Dependencies may import unselected tool modules for their pure helpers.
mcp.enabled_tool_modules = {f"{__name__}.{module}" for module in ENABLED_MODULES}
for _m in ENABLED_MODULES:
    importlib.import_module(f".{_m}", __name__)
