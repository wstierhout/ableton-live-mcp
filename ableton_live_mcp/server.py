"""Server entrypoint: run the MCP server, or a setup subcommand.

The tool modules and the FastMCP app are imported inside main(), after the
subcommand check, so `install`, `uninstall`, `doctor`, and `--version` run
without loading the full tool surface (or even needing the `mcp` dependency).
"""

import logging
import sys

_SUBCOMMANDS = frozenset(
    {"install", "uninstall", "doctor", "--help", "-h", "help", "--version", "-V", "version"}
)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        from . import cli

        sys.exit(cli.run(sys.argv[1:]))

    from . import tools  # noqa: F401  (importing registers every @mcp.tool)
    from .app import mcp

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
