"""Setup helpers exposed as subcommands: install, uninstall, doctor.

These make onboarding a one-liner instead of a manual file copy, and give users
a way to diagnose a broken setup.
"""

import platform
import socket
from importlib import resources
from pathlib import Path

from . import __version__
from .connection import ABLETON_HOST, ABLETON_PORT

SCRIPT_FOLDER = "AbletonMCP"


def _remote_scripts_dir() -> Path:
    home = Path.home()
    if platform.system() == "Windows":
        return home / "Documents" / "Ableton" / "User Library" / "Remote Scripts"
    return home / "Music" / "Ableton" / "User Library" / "Remote Scripts"


def _remote_script_source() -> bytes:
    # The Remote Script ships inside this package, so install works offline and
    # the copied script always matches the installed server version. Read it as
    # data (navigate from the package root) rather than importing it - the module
    # imports Live's _Framework, which only exists inside Ableton.
    script = resources.files(__package__).joinpath("remote_script", "__init__.py")
    return script.read_bytes()


def install() -> int:
    dest_dir = _remote_scripts_dir() / SCRIPT_FOLDER
    parent = dest_dir.parent
    if not parent.exists():
        print(f"Could not find Ableton's Remote Scripts folder at:\n  {parent}")
        print(
            "Is Ableton Live installed? If your User Library is elsewhere, copy the "
            "Remote Script there by hand."
        )
        return 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "__init__.py").write_bytes(_remote_script_source())
    print(f"Installed the Remote Script to:\n  {dest_dir / '__init__.py'}\n")
    print("Next:")
    print("  1. Restart Ableton Live.")
    print("  2. Settings > Link/Tempo/MIDI > Control Surface: AbletonMCP (Input/Output: None).")
    print("  3. Run `ableton-live-mcp doctor` to confirm the connection.")
    return 0


def uninstall() -> int:
    dest = _remote_scripts_dir() / SCRIPT_FOLDER / "__init__.py"
    if dest.exists():
        dest.unlink()
        try:
            dest.parent.rmdir()
        except OSError:
            pass
        print(f"Removed {dest}. Restart Live to unload it.")
    else:
        print("Nothing to remove; the Remote Script was not found.")
    return 0


def doctor() -> int:
    print(f"ableton-live-mcp {__version__} setup check\n")
    ok = True

    installed = (_remote_scripts_dir() / SCRIPT_FOLDER / "__init__.py").exists()
    print(f"  [{'ok' if installed else 'x '}] Remote Script installed in the User Library")
    if not installed:
        print("       fix: run `ableton-live-mcp install`")
        ok = False

    try:
        with socket.create_connection((ABLETON_HOST, ABLETON_PORT), timeout=3) as s:
            s.sendall(b'{"type": "get_session_info", "params": {}}')
            data = s.recv(8192)
        reachable = b'"status"' in data
    except Exception:
        reachable = False
    print(
        f"  [{'ok' if reachable else 'x '}] Remote Script reachable on "
        f"{ABLETON_HOST}:{ABLETON_PORT}"
    )
    if not reachable:
        print("       fix: start Ableton Live, select AbletonMCP as the Control Surface,")
        print("            and dismiss any open dialog (the trial nag blocks the script).")
        ok = False

    print()
    print("All good." if ok else "Some checks failed; follow the fixes above.")
    return 0 if ok else 1


USAGE = """ableton-live-mcp - Ableton Live MCP server

Usage:
  ableton-live-mcp                 run the MCP server (this is what MCP clients call)
  ableton-live-mcp install         copy the Remote Script into Ableton's User Library
  ableton-live-mcp uninstall       remove the Remote Script
  ableton-live-mcp doctor          check that the setup is working
  ableton-live-mcp --version       print the version
  ableton-live-mcp --help          show this help
"""


def run(argv) -> int:
    cmd = argv[0]
    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if cmd in ("-V", "--version", "version"):
        print(__version__)
        return 0
    handlers = {"install": install, "uninstall": uninstall, "doctor": doctor}
    if cmd not in handlers:
        print(f"Unknown command: {cmd}\n\n{USAGE}")
        return 2
    return handlers[cmd]()
