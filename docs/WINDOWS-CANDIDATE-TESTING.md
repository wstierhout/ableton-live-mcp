# Windows acceptance: AbletonMCP 2.0.0rc1

This is an unpublished release candidate, including PR #4 (automation durations
and ramps), #5 (sample automation), and the combined #6/#7 parameter conversion
fixes. PR #8 arrangement automation is deferred. The complete tool surface is
157 tools. Passing the offline checks does not prove that actual Windows Live
acceptance has passed.

Use a disposable set and stock devices. Keep real projects closed. Record the
Windows edition/build, CPU architecture, Python version, installed Live edition
and exact Live version. Run this on the Live version already on the machine;
mark the other Live major version untested unless you actually install and test
it. The release supports Live 11 and 12, so a single Live 12 run does not establish
Live 11 compatibility. Do not test on Windows Server as a substitute for Windows
11 acceptance.

## 1. Transfer the exact candidate and verify it

Use the candidate artifact supplied with this handoff, plus its `SHA256SUMS`.
Do **not** install `mcp-server-ableton-live` by name from PyPI or clone today's
`main`: those can resolve to the older release instead of this candidate.
A source archive, pure Python wheel and Windows native wheel are distinct
artifacts. Only a `win_amd64` wheel matching your Python ABI establishes the
Windows native lane; a `macosx` or `manylinux` wheel cannot run on Windows.

Put the delivered files in a new directory, for example
`$HOME\AbletonMCP-2.0.0rc1`. Check the SHA-256 of **each** wheel/archive/script
against the supplied manifest before extracting or running it:

```powershell
$Root = Join-Path $HOME 'AbletonMCP-2.0.0rc1'
Set-Location $Root
Get-FileHash -Algorithm SHA256 -LiteralPath '.\mcp_server_ableton_live-2.0.0rc1-py3-none-any.whl'
Get-Content -LiteralPath '.\SHA256SUMS'
# Compare the complete 64-character digest and the exact filename.
# Repeat for the source archive and any separately supplied Windows wheel.
```

Record the candidate source SHA/manifest as well as the version: two unpublished
builds could otherwise use `2.0.0rc1` while containing different bytes. A file's
name alone does not prove its identity. Keep the original verified artifacts.
Extract the source archive to `$Root\source`, adjusting that path for any archive
wrapper directory. It must contain `pyproject.toml`, `docs`, `scripts` and `tests`.
Keep the environment outside the extracted source directory.

## 2. Install into an isolated environment

The supported Python minimum is 3.10. Python 3.13 x64 is a convenient baseline
for Windows x64, if installed. Check available installations first:

```powershell
py -0p
py -3.13 -m venv "$Root\.venv-python"
$Py = Join-Path $Root '.venv-python\Scripts\python.exe'
& $Py -I -m pip install --upgrade pip
& $Py -I -m pip install "$Root\mcp_server_ableton_live-2.0.0rc1-py3-none-any.whl"
& $Py -I -m pip check
& $Py -I -m ableton_live_mcp.server --version
```

If you already use `uv`, the equivalent is `uv venv --python 3.13
"$Root\.venv-python"`, then `uv pip install --python $Py <exact-local-wheel>`.
Neither route needs an activated environment. Do not run the candidate through
an unpinned `uvx` command.

The pure wheel requires no Microsoft C/C++ compiler. Its optional audio kernel
must report `native_audio: false`; that is expected, not native evidence. Run the
read-only verifier from the extracted source:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\source\scripts\test-windows-candidate.ps1" `
  -Python $Py -ExpectedAudio Python -OutputDirectory "$Root\evidence-python"
```

`-ExecutionPolicy Bypass` applies only to that child PowerShell invocation; it
does not change the machine policy. The verifier blocks sockets in its Python
process, checks package/bridge identity, all 157 tools and the selected audio
backend. It neither installs a bridge nor contacts Live. Its `environment.json`
contains package paths, versions, hashes and a direct local-install URL; inspect
it before sharing if those paths identify your Windows account.

For a delivered CPython 3.13 x64 Windows native wheel, use a **second** environment:

```powershell
py -3.13 -m venv "$Root\.venv-native"
$NativePy = Join-Path $Root '.venv-native\Scripts\python.exe'
# Use the exact delivered win_amd64 filename; never substitute a Mac/Linux wheel.
& $NativePy -I -m pip install "$Root\mcp_server_ableton_live-2.0.0rc1-cp313-cp313-win_amd64.whl"
& $NativePy -I -m pip check
powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\source\scripts\test-windows-candidate.ps1" `
  -Python $NativePy -ExpectedAudio Native -OutputDirectory "$Root\evidence-native"
```

If no matching Windows wheel was supplied, native acceptance is **pending**.
A source installation can silently fall back to Python when a compiler is
missing, so a successful source install does not prove native support.
When intentionally testing a source build without a compiler, use a third fresh
venv and `ABLETON_MCP_PURE_PYTHON=1` only during the local source installation;
remove it afterwards. It is a build setting, not a runtime switch for an already
installed native wheel.

Optional full offline suite, when the source handoff includes tests:

```powershell
& $Py -I -m pip install 'pytest>=8' 'ruff>=0.8'
Push-Location "$Root\source"
try {
  & $Py -I -m ruff check .
  & $Py -I -m ruff format --check .
  & $Py -I -m pytest --import-mode=importlib -q
  # These tests simulate Live; they are not Live acceptance.
} finally { Pop-Location }
```

Native parity tests may be skipped in the pure lane. For a native wheel, install
pytest into its venv and run `test_audio_native.py` with the native interpreter;
**all skips in that file require explanation**, not a native pass label. Copy
stdout/stderr, totals and any skips into the evidence directory. Automated
release checks must already have passed after the Opus audit before this handoff;
these Windows checks add platform-specific evidence.

## 3. Install the matching Live bridge explicitly

In Live, locate the actual **User Library** under Settings/Preferences > Library.
It may be on OneDrive or a custom disk. Set that exact directory here, not the
`Remote Scripts` subdirectory:

```powershell
$Library = 'C:\Users\YOUR-NAME\Documents\Ableton\User Library'
& $Py -I -m ableton_live_mcp.server install --user-library $Library
```

Close Live before replacement. If installation reports an existing different
`AbletonMCP` script, review it and back up the **whole existing folder** to a
location outside `Remote Scripts` before using:

```powershell
# Review the existing folder first. This command additionally makes a .bak copy
# of the old __init__.py and prints its recovery location.
& $Py -I -m ableton_live_mcp.server install --replace --user-library $Library
```

Do not leave a renamed backup bridge folder enabled inside `Remote Scripts`.
Do not enable two bridges on the same port. Fully restart Live; then select
**AbletonMCP** once under Settings > Tempo & MIDI > MIDI > Control Surface,
with Input/Output **None**. Older Live builds call the page Link/Tempo/MIDI or
Link/MIDI. Dismiss any trial, save or licence dialogs.

```powershell
& $Py -I -m ableton_live_mcp.server doctor --user-library $Library --json |
  Tee-Object -FilePath "$Root\evidence-python\doctor.json"
```

Required: `ok: true`, `package_version` and `running_script_version` both
`2.0.0rc1`, `script_matches_package: true`, `running_script_matches: true`,
`reachable: true`, protocol at least 2, and the expected actual `live_version`.
Save this JSON. `doctor` makes a read-only request to the running bridge; it does
not prove the automation or recording tests. A bridge matching only the version
string does not establish byte identity: retain the installed/package SHA check
as well. Re-run the verifier with `-UserLibrary $Library` to capture installed
bytes, without connecting to Live.

Use `ABLETON_HOST=127.0.0.1` and `ABLETON_PORT=9877` in the test process. Do not
open that port to the network or add a firewall exception for remote clients.

## 4. Prepare a deterministic disposable set

Save a new set as `MCP20-Windows-Live-<exact-version>.als` in a temporary project.
Use a working audio driver and enable Live's audio engine. Stop transport and
recording, disable the Arrangement loop and disarm every track. Keep volume low.
Do not reorder tracks/devices while a test is running.

Prepare this fixture using Live's UI; remove existing default return effects so
these indices are exact:

| Target | Device position and content |
| --- | --- |
| Regular track 0, named `MCP20 TEST MIDI` | Simpler at device 0, Utility at device 1, Audio Effect Rack at device 2 |
| Rack on track 0/device 2 | One chain (index 0), containing one Utility at chain-device 0 |
| Return A (return index 0) | Utility at device 0 |
| Main/Master | Utility at device 0 |
| Track 0 Session slot 0 | Empty slot initially; the test creates a four-beat MIDI clip |

Use unmodified copies of the same stock Utility. The examples address its
`Gain` parameter by its returned name, never by a guessed parameter index.
If your edition does not provide the fixture devices, record that limitation
and adapt the documented indices after reading the device lists. Do not call
a missing rack test passed. Save a screenshot of this prepared fixture.

## 5. Exercise MCP calls through the installed candidate

Save the following as `$Root\live-check.py` **outside** the extracted source.
It starts the installed server through the MCP Python SDK. This is deliberately
separate from the offline PowerShell verifier: it edits the disposable set.
Start with `inventory`; then run the remaining cases separately, retaining each
log. Do not operate another MCP client against Live during these runs.

```python
import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

TARGET = {"track_index": 0, "clip_index": 0, "device_index": 1, "parameter": "Gain"}

async def exercise(session, case):
    async def call(name, args=None, error=False):
        reply = await session.call_tool(name, args or {})
        print(json.dumps({"tool": name, "arguments": args or {},
                          "reply": reply.model_dump(mode="json")}), flush=True)
        assert bool(reply.isError) == error, f"Unexpected result from {name}"
        payload = reply.structuredContent
        assert isinstance(payload, dict) and "data" in payload, "Missing structured result"
        if error:
            assert payload.get("error"), "Missing structured failure"
            return payload["error"]
        assert payload.get("error") is None
        return payload["data"]

    state = await call("get_session_info")
    assert state["server_version"] == state["bridge_version"] == "2.0.0rc1"
    assert state["bridge_protocol"] >= 2
    assert not state["is_playing"] and not state["record_mode"]
    snapshot = await call("get_session_snapshot")
    assert not any(t.get("armed") for t in snapshot["tracks"])
    track = await call("get_track_info", {"track_index": 0})
    assert track["name"] == "MCP20 TEST MIDI", "Wrong disposable fixture"
    top = await call("get_device_parameters", {"track_index": 0, "device_index": 1})
    gain = next(p for p in top["parameters"] if p["name"] == "Gain")
    assert gain["is_enabled"] and not gain["is_quantized"]
    lo, hi = gain["min"], gain["max"]
    low, high = lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.75

    if case == "inventory":
        tools = (await session.list_tools()).tools
        print(json.dumps({"tool_count": len(tools), "names": sorted(t.name for t in tools)}))
        assert len(tools) == 157
        await call("get_return_device_parameters", {"return_index": 0, "device_index": 0})
        await call("get_master_device_parameters", {"device_index": 0})
        await call("get_rack_chains", {"track_index": 0, "device_index": 2})
        return

    if case == "parameters":
        targets = [
            ("set_device_parameter", {"track_index": 0, "device_index": 1}),
            ("set_return_device_parameter", {"return_index": 0, "device_index": 0}),
            ("set_master_device_parameter", {"device_index": 0}),
            ("set_chain_device_parameter", {"track_index": 0, "device_index": 2,
                                           "chain_index": 0, "chain_device_index": 0}),
        ]
        db_results = []
        for name, address in targets:
            args = {**address, "parameter": "Gain"}
            number = await call(name, {**args, "value": low})
            string = await call(name, {**args, "value": str(low)})
            assert math.isclose(number["native"], string["native"], abs_tol=1e-5)
            db = await call(name, {**args, "value": "-12 dB"})
            assert "dB" in db["display"]
            observed = float(db["display"].replace("dB", "").strip())
            assert abs(observed + 12) <= 0.15, db
            db_results.append(db["native"])
            await call(name, {**args, "value": "not-a-parameter-value"}, error=True)
            same = await call(name, {**args, "value": "-12 dB"})
            assert math.isclose(same["native"], db["native"], abs_tol=1e-5)
            # Rejection must also leave the displayed value unchanged in Live.
            await call(name, {**args, "value": "-inf dB"})
            await call(name, {**args, "value": "nan"}, error=True)
            await call(name, {**args, "value": "inf"}, error=True)
            await call(name, {**args, "value": "-12 dB"})
        assert max(db_results) - min(db_results) <= 1e-5
        return

    if case == "automation":
        # Run this case once per freshly prepared fixture. Existing clips are not overwritten.
        assert not track["clip_slots"][0]["has_clip"], "Reopen the empty fixture first"
        await call("create_clip", {"track_index": 0, "clip_index": 0, "length": 4.0})
        baseline_notes = await call("get_clip_notes", {"track_index": 0, "clip_index": 0})
        await call("write_automation", {**TARGET, "points": [
            {"time": 0.0, "value": low}, {"time": 2.0, "value": high}]})
        times = [0.001, 1.999, 2.001, 3.999]
        before = await call("read_automation", {**TARGET, "times": times})
        # Expected values: low, low, high, high. Inspect the sample assertions below.
        assert_samples(before, [low, low, high, high], hi - lo)
        # Invalid explicit duration must be rejected before the existing envelope is cleared.
        await call("write_automation", {**TARGET, "points": [
            {"time": 0.0, "duration": 0.0, "value": low}]}, error=True)
        after = await call("read_automation", {**TARGET, "times": times})
        assert after == before, "Invalid request changed the existing envelope"
        assert await call("get_clip_notes", {"track_index": 0, "clip_index": 0}) == baseline_notes
        await call("write_automation", {**TARGET, "mode": "ramp", "step_length": 0.25,
            "points": [{"time": 0.0, "value": low}, {"time": 3.0, "value": high}]})
        ramp = await call("read_automation", {**TARGET, "times": [0.001, 1.501, 3.001, 3.999]})
        # Midpoint within one stair-step; final target must be reached and held.
        assert_samples(ramp, [low, (low + high) / 2, high, high], (hi - lo) * 2)
        await call("undo")
        restored = await call("read_automation", {**TARGET, "times": times})
        assert_samples(restored, [low, low, high, high], hi - lo)
        await call("redo")
        await call("read_automation", {**TARGET, "start": 0.0, "end": 4.0, "step": 1.0})
        await call("read_automation", {**TARGET, "times": [0.0], "step": 1.0}, error=True)
        return

    if case == "batch":
        original = track["name"]
        await call("batch_commands", {"commands": [
            {"type": "set_track_name", "params": {"track_index": 0, "name": "MUST NOT APPLY"}},
            {"type": "no_such_command", "params": {}}]}, error=True)
        assert (await call("get_track_info", {"track_index": 0}))["name"] == original
        operation = uuid.uuid4().hex
        commands = [{"type": "set_track_name", "params": {"track_index": 0, "name": "BATCH TEST"}},
                    {"type": "set_track_pan", "params": {"track_index": 0, "pan": 0.25}}]
        first = await call("batch_commands", {"commands": commands, "operation_id": operation})
        again = await call("batch_commands", {"commands": commands, "operation_id": operation})
        assert again == first
        await call("undo")
        restored = await call("get_track_info", {"track_index": 0})
        assert restored["name"] == original and abs(restored["panning"] - track["panning"]) < 1e-5
        error = await call("batch_commands", {"commands": [
            {"type": "set_track_name", "params": {"track_index": 0, "name": "PARTIAL TEST"}},
            {"type": "set_device_parameter", "params": {"track_index": 0, "device_index": 1,
              "parameter": "Gain", "value": "not-a-parameter-value"}}]}, error=True)
        assert error["phase"] == "execution" and error["completed_count"] == 1
        assert (await call("get_track_info", {"track_index": 0}))["name"] == "PARTIAL TEST"
        await call("undo")
        assert (await call("get_track_info", {"track_index": 0}))["name"] == original
        return

    if case == "cancel":
        assert not state["loop"], "Disable Arrangement loop first"
        original_ids = {t["id"] for t in snapshot["tracks"]}
        # MCP SDK 1.x exposes its request sequence internally. Capture the next ID
        # immediately before this one request; cancelling only the Python Task
        # does NOT send an MCP notifications/cancelled message in this SDK.
        request_id = session._request_id
        pending = asyncio.create_task(session.call_tool("record_section", {
            "start_beat": 0.0, "end_beat": 64.0, "cleanup": True}))
        await asyncio.sleep(0)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = await call("get_session_info")
            if current["is_playing"] and current["record_mode"]:
                break
            assert not pending.done(), "Recording ended or failed before cancellation"
            await asyncio.sleep(0.2)
        else:
            raise AssertionError("Recording never reached the active state; inspect Live")
        await session.send_notification(types.ClientNotification(types.CancelledNotification(
            params=types.CancelledNotificationParams(requestId=request_id, reason="Windows acceptance"))))
        try:
            reply = await asyncio.wait_for(pending, 30)
            print(json.dumps({"cancel_reply": reply.model_dump(mode="json")}))
        except McpError as exc:
            print(json.dumps({"cancel_rpc": str(exc)}))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = await call("get_session_info")
            settled = await call("get_session_snapshot")
            if (not current["is_playing"] and not current["record_mode"]
                    and {t["id"] for t in settled["tracks"]} == original_ids
                    and not any(t.get("armed") for t in settled["tracks"])):
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError("Cancellation recovery did not settle; stop Live manually and retain evidence")
        # A following edit demonstrates that cancellation released the edit queue.
        await call("set_track_name", {"track_index": 0, "name": "MCP20 TEST MIDI"})
        return

# read_automation returns sampled values rather than editable breakpoint events.
def assert_samples(result, expected, span):
    actual = [point["value"] for point in result["samples"]]
    assert len(actual) == len(expected)
    assert all(abs(a - b) <= max(abs(span) * 0.001, 1e-5)
               for a, b in zip(actual, expected)), (actual, expected)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=["inventory", "parameters", "automation", "batch", "cancel"])
    args = parser.parse_args()
    env = {**os.environ, "ABLETON_HOST": "127.0.0.1", "ABLETON_PORT": "9877"}
    env.pop("ABLETON_TOOLSETS", None)
    params = StdioServerParameters(command=sys.executable,
        args=["-I", "-m", "ableton_live_mcp.server"], env=env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as session:
            await session.initialize()
            await exercise(session, args.case)
    print(json.dumps({"case": args.case, "status": "passed"}), flush=True)

asyncio.run(main())
```

Run from the artifact directory, using the installed environment:

```powershell
& $Py -I "$Root\live-check.py" inventory 2>&1 | Tee-Object "$Root\evidence-python\inventory.log"
& $Py -I "$Root\live-check.py" parameters 2>&1 | Tee-Object "$Root\evidence-python\parameters.log"
& $Py -I "$Root\live-check.py" automation 2>&1 | Tee-Object "$Root\evidence-python\automation.log"
& $Py -I "$Root\live-check.py" batch 2>&1 | Tee-Object "$Root\evidence-python\batch.log"
& $Py -I "$Root\live-check.py" cancel 2>&1 | Tee-Object "$Root\evidence-python\cancel.log"
```

An exception, missing final `"status": "passed"`, or nonzero Python exit code
means the case failed. Save the complete log and stop dependent tests. Do not
retry an uncertain mutation until inspecting the current state; the bridge keeps
only a bounded receipt history. To rerun automation, reopen the saved empty-clip
fixture rather than deleting an unknown current clip. Restarting Live resets
stable IDs and the receipt history; establish a fresh baseline after a restart.

## 6. Inspect the actual Live result

The SDK checks are necessary but do not establish audible or visual correctness.
Keep screenshots and a short written result for these observations:

- All four Utility Gain controls display approximately **-12 dB** for the finite
  display-string request. The `-inf dB` test reaches the mute endpoint. Native
  numeric values and their numeric-string equivalents produce the same readback.
  Invalid strings leave the value unchanged; inspect the UI while that call runs.
- Session clip envelope steps remain low before beat 2 and high after beat 2.
  The ramp has visible finite-width stairs, reaches the final value, and holds it
  through the end. Readback samples around boundaries use epsilon beats to avoid
  confusing an exact event boundary with a vanished zero-duration segment.
- One Undo after the ramp restores the previous step envelope; Redo restores the
  ramp. There must be no unrelated note/clip loss. The batch's single Undo restores
  both its name and pan, while its runtime failure reports the completed first edit.
- During cancellation, recording starts before cancellation is sent; afterwards
  transport is stopped, Arrangement recording is off, no track remains armed,
  the owned temporary Bounce track is removed, and original tracks survive.
  Audio files may remain in the disposable project's recording directory. A
  successful next edit confirms that the edit queue recovered.

Also run a short successful `record_section` (for example beats 0–4) on a
**separate disposable arrangement containing an audible stock-device MIDI clip**.
Check `transport_recovery.confirmed_stopped`, the returned WAV path and
`cleanup_errors`; open/play that WAV and confirm it contains the expected sound.
File existence, an empty recording, cloud RDP audio redirection, or a synthetic
offline audio fixture is not proof of Live recording correctness. If the cloud
host cannot provide a working audio engine, report recording/audio acceptance as
blocked while retaining the control/automation results.

Before calling the version release-ready, retain:

| Evidence | Required record |
| --- | --- |
| Candidate identity | Artifact SHA256, source revision/manifest, package version/path |
| Windows/Python | OS edition/build, architecture, exact Python and dependencies |
| Audio lane | `native_audio` true/false, native module path when applicable, actual skips |
| Live identity | Edition and exact 11.x/12.x version; untested major explicitly marked |
| Bridge | Installed/package SHA match, running version/protocol, successful doctor JSON |
| PR acceptance | Parameters, automation durations/ramps/readback, invalid preservation, Undo, batch logs |
| Recovery | Successful recording, cancellation log, audible WAV check, no leaked armed tracks |
| Human observations | Device/envelope screenshots, freezes/crashes/dialogs, expected vs actual |

Repeat the actual Live cases with `$NativePy` if testing the native lane, then
repeat against the other supported Live major version when available. A local
Mac pass plus a Windows Python-fallback pass still leaves Windows native
acceptance unverified. The Opus audit and automated test report supplied with
this candidate are separate evidence and must not be relabelled Windows Live
passes.

## Recovery and cleanup

Close the candidate MCP client process and Live. Keep evidence and the candidate
artifacts. If returning to a previous bridge, restore the reviewed backup folder,
fully restart Live and reselect its Control Surface. The candidate's `uninstall`
command removes only a byte-identical installed script and makes a recovery copy;
it refuses to remove an unrelated modified bridge. Do not use it to delete a
bridge belonging to another version. Remove disposable projects only after
keeping any failure reproductions and WAVs needed for diagnosis.
