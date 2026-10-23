# Read-only package/environment evidence. Does not connect to Live, install a
# package, change a bridge, or change an MCP client configuration.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$UserLibrary = "",
    [string]$ExpectedVersion = "2.0.0rc1",
    [ValidateSet("Any", "Native", "Python")][string]$ExpectedAudio = "Any",
    [string]$OutputDirectory = (Join-Path (Get-Location) ("windows-evidence-" + (Get-Date -Format "yyyyMMdd-HHmmss")))
)
$ErrorActionPreference = "Stop"
$PythonPath = (Resolve-Path -LiteralPath $Python).Path
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$OutputPath = (Resolve-Path -LiteralPath $OutputDirectory).Path
$ReportPath = Join-Path $OutputPath "environment.json"
$ErrorPath = Join-Path $OutputPath "environment.stderr.txt"

# -I excludes the current checkout and PYTHONPATH from package resolution. The
# Python-side socket guards turn accidental network access into a hard failure.
$Probe = @'
import array
import ast
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import pathlib
import platform
import socket
import sys
import traceback

expected, expected_audio, user_library = sys.argv[1:]
report = {
    "expected_version": expected,
    "expected_audio": expected_audio,
    "python": sys.version,
    "python_executable": sys.executable,
    "architecture": platform.machine(),
    "platform": platform.platform(),
    "windows_version": platform.win32_ver(),
    "checks": {},
    "network_access": "blocked in this verification process",
    "live_acceptance": "not run; this script does not contact Live",
}

def deny_network(*args, **kwargs):
    raise AssertionError("Network access attempted during offline verification")

socket.create_connection = deny_network
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
os.environ.pop("ABLETON_TOOLSETS", None)
os.environ["ABLETON_HOST"] = "127.0.0.1"
os.environ["ABLETON_PORT"] = "1"

try:
    import ableton_live_mcp
    from ableton_live_mcp import audio_stats, tools
    from ableton_live_mcp.app import mcp

    package_root = pathlib.Path(ableton_live_mcp.__file__).resolve().parent
    metadata = importlib.metadata.distribution("mcp-server-ableton-live")
    report.update(
        package_version=ableton_live_mcp.__version__,
        distribution_version=metadata.version,
        package_path=str(package_root),
        direct_url=json.loads(metadata.read_text("direct_url.json") or "null"),
        dependency_versions={
            name: importlib.metadata.version(name)
            for name in ("mcp", "pydantic", "av", "anyio")
        },
        native_audio=audio_stats.NATIVE_AUDIO,
    )
    assert ableton_live_mcp.__version__ == metadata.version == expected
    report["checks"]["package_identity"] = True
    if expected_audio != "Any":
        assert audio_stats.NATIVE_AUDIO == (expected_audio == "Native"), (
            "Audio backend differs from the requested evidence lane"
        )
    native_spec = importlib.util.find_spec("ableton_live_mcp._audio_native")
    report["native_module_path"] = native_spec.origin if native_spec else None

    source = (package_root / "remote_script" / "__init__.py").read_bytes()
    syntax = ast.parse(source.decode("utf-8"), feature_version=(3, 7))
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in syntax.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"BRIDGE_VERSION", "BRIDGE_PROTOCOL"}
    }
    report.update(
        packaged_bridge_sha256=hashlib.sha256(source).hexdigest(),
        packaged_bridge_version=constants["BRIDGE_VERSION"],
        packaged_bridge_protocol=constants["BRIDGE_PROTOCOL"],
    )
    assert constants["BRIDGE_VERSION"] == expected
    assert constants["BRIDGE_PROTOCOL"] >= 2
    report["checks"]["bridge_identity_and_python37_syntax"] = True
    if user_library != "-":
        installed = pathlib.Path(user_library) / "Remote Scripts" / "AbletonMCP" / "__init__.py"
        report["installed_bridge_path"] = str(installed)
        report["installed_bridge_exists"] = installed.is_file()
        if installed.is_file():
            report["installed_bridge_sha256"] = hashlib.sha256(installed.read_bytes()).hexdigest()
            report["installed_bridge_matches_package"] = installed.read_bytes() == source

    registered = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in registered}
    report["tool_count"] = len(registered)
    report["tool_names"] = sorted(names)
    assert len(registered) == 157, "Unexpected tool count; verify the candidate identity"
    assert {"write_automation", "read_automation", "set_chain_device_parameter"} <= names
    assert not {name for name in names if "arrangement" in name and "automation" in name}
    schemas = {tool.name: tool.inputSchema for tool in registered}
    assert {"mode", "step_length"} <= schemas["write_automation"]["properties"].keys()
    assert {"times", "start", "end", "step"} <= schemas["read_automation"]["properties"].keys()
    report["checks"]["full_tool_contract_and_pr8_exclusion"] = True

    samples = [-1.0, -0.5, 0.0, 0.25, 1.0]
    packed = array.array("d", samples).tobytes()
    expected_stats = audio_stats.python_float_stats(packed, len(samples))
    actual_stats = audio_stats.float_stats(packed, len(samples))
    assert all(math.isclose(a, b, rel_tol=1e-14, abs_tol=1e-14)
               for a, b in zip(actual_stats, expected_stats))
    pcm = b"".join(value.to_bytes(2, "little", signed=True)
                   for value in (-32768, -12345, 0, 12345, 32767))
    initial = ([0.0], [0.0], [0.0], [0.0])
    assert audio_stats.pcm_accumulate(pcm, 2, 1, *initial) == (
        audio_stats.python_pcm_accumulate(pcm, 2, 1, *initial)
    )
    report["checks"]["selected_audio_backend_smoke"] = True
    report["ok"] = True
except Exception as exc:
    report["ok"] = False
    report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    traceback.print_exc(file=sys.stderr)
print(json.dumps(report, indent=2))
sys.exit(0 if report["ok"] else 1)
'@
$LibraryArgument = if ($UserLibrary) { $UserLibrary } else { "-" }
$ErrorActionPreference = "Continue"
$Probe | & $PythonPath -I - $ExpectedVersion $ExpectedAudio $LibraryArgument 2> $ErrorPath |
    Set-Content -LiteralPath $ReportPath -Encoding UTF8
$ProbeExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
Get-Content -LiteralPath $ReportPath
Write-Host "Evidence: $OutputPath"
if ($ProbeExit -ne 0) {
    throw "Offline environment verification failed. Read environment.json and environment.stderr.txt."
}
Write-Host "Offline verification passed. Actual Live checks remain a separate step."
