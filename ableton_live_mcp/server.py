"""Server entrypoint.

Importing ableton_live_mcp.tools registers every @mcp.tool with the FastMCP app.
"""

import logging

from . import tools  # noqa: F401  (tool registration side effect)
from .app import mcp


def main():
    """Run the MCP server, or a setup subcommand (install/uninstall/doctor)."""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in (
        "install",
        "uninstall",
        "doctor",
        "--help",
        "-h",
        "help",
        "--version",
        "-V",
        "version",
    ):
        from . import cli

        sys.exit(cli.run(sys.argv[1:]))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
