"""Explicit, recoverable Remote Script setup. Never edits MCP client configuration."""

import argparse
import hashlib
import json
import os
import platform
import socket
import tempfile
import time
from importlib import resources
from pathlib import Path

from . import __version__
from .connection import ABLETON_HOST, ABLETON_PORT

SCRIPT_FOLDER = "AbletonMCP"


def _remote_scripts_dir(user_library=None):
    override = user_library or os.environ.get("ABLETON_USER_LIBRARY")
    if override:
        return Path(override).expanduser().absolute() / "Remote Scripts"
    base = Path.home() / ("Documents" if platform.system() == "Windows" else "Music")
    return base / "Ableton" / "User Library" / "Remote Scripts"


def _remote_script_source():
    return resources.files(__package__).joinpath("remote_script", "__init__.py").read_bytes()


def _target(user_library=None):
    folder = _remote_scripts_dir(user_library) / SCRIPT_FOLDER
    for candidate in (folder.parent, folder, folder / "__init__.py"):
        if candidate.is_symlink():
            raise ValueError(f"Refusing a symlink at {candidate}; use a real User Library path.")
    return folder / "__init__.py"


def _backup(dest):
    with tempfile.NamedTemporaryFile(
        prefix="__init__.py.", suffix=".bak", dir=dest.parent, delete=False
    ) as backup:
        backup.write(dest.read_bytes())
        return Path(backup.name)


def install(user_library=None, replace=False):
    dest = _target(user_library)
    if not dest.parent.parent.parent.is_dir():
        raise ValueError(
            "User Library not found. Check Live Settings, then use "
            "install --user-library '/path/to/User Library'."
        )
    source = _remote_script_source()
    if dest.exists() and dest.read_bytes() == source:
        print(f"Remote Script already matches {__version__}: {dest}")
        return 0
    if dest.exists() and not replace:
        raise ValueError(
            f"A different Remote Script exists at {dest}. It may belong to "
            "another Ableton MCP. Review it, then use install --replace to "
            "back up and replace it. Do not enable two bridges on the same port."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"Recovery copy: {_backup(dest)}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(source)
        temporary.chmod(0o644)
        temporary.replace(dest)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    print(
        f"Installed {__version__}: {dest}\nRestart Live. Select AbletonMCP in Settings > "
        "Tempo & MIDI > MIDI > Control Surface, Input/Output None. "
        "Older Live versions: Link, Tempo & MIDI or Link/MIDI > MIDI. Then run doctor."
    )
    return 0


def uninstall(user_library=None):
    dest = _target(user_library)
    if not dest.exists():
        print("Nothing to remove.")
        return 0
    if dest.read_bytes() != _remote_script_source():
        raise ValueError(
            "Installed script differs from this package. Refusing to remove it; "
            "review and remove it manually if intended."
        )
    backup = _backup(dest)
    dest.unlink()
    print(f"Removed {dest}. Recovery copy: {backup}. Restart Live to unload it.")
    return 0


def _probe():
    with socket.create_connection((ABLETON_HOST, ABLETON_PORT), timeout=3) as connection:
        connection.settimeout(15)
        deadline = time.monotonic() + 15
        connection.sendall(b'{"type":"get_session_info","params":{}}')
        data = bytearray()
        while len(data) < 1024 * 1024:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Bridge diagnostic deadline exceeded")
            connection.settimeout(remaining)
            chunk = connection.recv(8192)
            if not chunk:
                break
            data.extend(chunk)
            try:
                reply = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(reply, dict) or reply.get("status") != "success":
                raise ValueError("Remote Script returned an error, not a successful reply.")
            result = reply.get("result")
            if not isinstance(result, dict) or "tempo" not in result:
                raise ValueError("Unexpected service on the configured Ableton port.")
            return result
    raise ValueError("Incomplete or oversized JSON reply from Remote Script.")


def doctor(user_library=None, json_output=False):
    dest = _target(user_library)
    source = _remote_script_source()
    installed = dest.is_file()
    report = {
        "package_version": __version__,
        "script_path": str(dest),
        "script_installed": installed,
        "script_matches_package": installed and dest.read_bytes() == source,
        "expected_sha256": hashlib.sha256(source).hexdigest(),
        "host": ABLETON_HOST,
        "port": ABLETON_PORT,
        "reachable": False,
        "running_script_matches": False,
    }
    try:
        live = _probe()
        report.update(
            reachable=True,
            live_version=live.get("live_version"),
            running_script_version=live.get("bridge_version"),
            running_script_matches=(
                live.get("bridge_version") == __version__ and live.get("bridge_protocol", 1) >= 2
            ),
            bridge_protocol=live.get("bridge_protocol", 1),
            capabilities=live.get("capabilities", []),
            bridge_update_required=live.get("bridge_protocol", 1) < 2,
        )
    except (OSError, ValueError) as exc:
        report["error"] = str(exc)
    report["ok"] = bool(
        report["script_matches_package"]
        and report["reachable"]
        and report["running_script_matches"]
    )
    if json_output:
        print(json.dumps(report, indent=2))
    else:
        print(f"ableton-live-mcp {__version__} setup check\nScript: {dest}")
        for key in (
            "script_installed",
            "script_matches_package",
            "reachable",
            "running_script_matches",
        ):
            print(f"  [{'ok' if report[key] else 'x '}] {key.replace('_', ' ')}")
        print(
            "All good."
            if report["ok"]
            else "Check the User Library path, install the matching script (review --replace), "
            "restart Live, select AbletonMCP and dismiss modal dialogs. "
            "An older/different bridge cannot pass the running-version check."
        )
    return 0 if report["ok"] else 1


def run(argv):
    parser = argparse.ArgumentParser(
        prog="ableton-live-mcp", description="Run without arguments as an MCP stdio server."
    )
    parser.add_argument("--version", "-V", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "uninstall", "doctor"):
        sub = commands.add_parser(name)
        sub.add_argument("--user-library", help="User Library directory, not Remote Scripts")
        if name == "install":
            sub.add_argument(
                "--replace",
                action="store_true",
                help="Back up and replace a different existing script",
            )
        if name == "doctor":
            sub.add_argument("--json", dest="json_output", action="store_true")
    if argv == ["version"]:
        argv = ["--version"]
    if argv == ["help"]:
        argv = ["--help"]
    try:
        args = vars(parser.parse_args(argv))
        command = args.pop("command")
        return {"install": install, "uninstall": uninstall, "doctor": doctor}[command](**args)
    except (OSError, ValueError) as exc:
        print(f"Setup failed: {exc}")
        return 1
