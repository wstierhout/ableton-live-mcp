"""Server entrypoint: run the MCP server, or a setup subcommand.

The tool modules and the FastMCP app are imported inside main(), after the
subcommand check, so `install`, `uninstall`, `doctor`, and `--version` run
without loading the full tool surface (or even needing the `mcp` dependency).
"""

import logging
import os
import sys


def main():
    if len(sys.argv) > 1:
        # Forward every subcommand to the CLI - including unknown ones, so a
        # typo prints usage instead of silently blocking on the stdio server.
        from . import cli

        sys.exit(cli.run(sys.argv[1:]))

    logging.basicConfig(
        level=os.environ.get("ABLETON_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    from . import tools  # noqa: F401  (importing registers every @mcp.tool)
    from .app import mcp

    mcp.run()


if __name__ == "__main__":
    main()
