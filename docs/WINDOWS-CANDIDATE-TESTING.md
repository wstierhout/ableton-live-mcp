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
The source handoff must be a **`git archive` of the exact committed candidate**
identified in the handoff manifest. A packaging sdist is not a substitute: it
may omit `tests`. A source archive, pure Python wheel and Windows native wheel
are distinct artifacts. Only a `win_amd64` wheel matching your Python ABI establishes the
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
Extract the supplied exact-commit Git archive to `$Root\source`, adjusting
that path for any archive wrapper directory. It must contain `pyproject.toml`,
`docs`, `scripts` and `tests`. The handoff producer creates it with `git archive`
from the recorded candidate commit, not an uncommitted checkout or a PyPI sdist.
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

Optional full offline suite, using the exact-commit Git archive above:

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
Start with no active or overridden automation on any selected Utility control;
its `automation_state` must be exactly `0`, as recorded by the runner. Do not
reorder tracks/devices while a test is running.

Prepare this fixture using Live's UI; remove existing default return effects so
these indices are exact:

| Target | Device position and content |
| --- | --- |
| Regular track 0, named `MCP20 TEST MIDI` | Simpler at device 0, Utility at device 1, Audio Effect Rack at device 2 |
| Rack on track 0/device 2 | One chain (index 0), containing one Utility at chain-device 0 |
| Return A (return index 0) | Utility at device 0 |
| Main/Master | Utility at device 0 |
| Track 0 Session slot 0 | Empty slot initially; the test creates a four-beat MIDI clip |

Use unmodified copies of the same stock Utility. Its gain control can be
returned as `Gain` or `Output`, depending on the Live/device version. The runner
discovers exactly one enabled, continuous parameter with either name separately
for the regular, return and Master copies, records each selected name/index in
the log, and aborts if a target is missing or ambiguous. Native ranges must match.
There is no nested-device parameter getter: before any parameter edits, the
parameters case asks you to confirm that the rack-chain copy is the identical,
unmodified stock Utility with no active/overridden automation, by typing
`SAME UTILITY`. It then uses the matching parameter index actually returned for
the regular copy; it never guesses a nested name or index. This also avoids
assuming the UI label matches Live's API name. A mismatch stops before edits.
If your edition does not provide the fixture devices, record that limitation
and adapt the documented indices after reading the device lists. Do not call
a missing rack test passed. Save a screenshot of this prepared fixture.

## 5. Exercise MCP calls through the installed candidate

Use the supplied `$Root\source\scripts\test-live-candidate.py` from the
verified source archive. No Python code needs to be copied from this guide. The
runner starts the installed server through the MCP Python SDK. This is deliberately
separate from the offline PowerShell verifier: it edits the disposable set.
Start with `inventory`; then run the remaining cases separately, retaining each
log. Reopen the saved pristine fixture before **each** case: the automation
cases create a clip and leave an envelope. Do not operate another MCP client
against Live during these runs.

Run from the artifact directory, using the installed environment:

```powershell
& $Py -I "$Root\source\scripts\test-live-candidate.py" inventory 2>&1 | Tee-Object "$Root\evidence-python\inventory.log"
& $Py -I "$Root\source\scripts\test-live-candidate.py" parameters 2>&1 | Tee-Object "$Root\evidence-python\parameters.log"
# Reopen the saved pristine fixture before each following case.
& $Py -I "$Root\source\scripts\test-live-candidate.py" automation 2>&1 | Tee-Object "$Root\evidence-python\automation.log"
# Reopen it again: beat-zero POINTS acceptance needs a separate fresh clip.
& $Py -I "$Root\source\scripts\test-live-candidate.py" automation-points-zero 2>&1 | Tee-Object "$Root\evidence-python\automation-points-zero.log"
& $Py -I "$Root\source\scripts\test-live-candidate.py" batch 2>&1 | Tee-Object "$Root\evidence-python\batch.log"
& $Py -I "$Root\source\scripts\test-live-candidate.py" cancel 2>&1 | Tee-Object "$Root\evidence-python\cancel.log"
```

The parameters case pauses twice: first for the identical nested Utility
confirmation above, then for manual rack-chain preservation inspection. Type
`PASS` at the second prompt only if its corresponding gain control still
displays -12 dB after the rejected call.
An exception, missing final `"status": "passed"`, or nonzero Python exit code
means the case did not pass. Save the complete log and stop dependent tests. Do not
retry an uncertain mutation until inspecting the current state; the bridge keeps
only a bounded receipt history. To rerun automation, reopen the saved empty-clip
fixture rather than deleting an unknown current clip. Restarting Live resets
stable IDs and the receipt history; establish a fresh baseline after a restart.

Beat-zero points and ramps both set the parameter's start value and wait for
it to settle before writing their envelope. This changes the stopped/manual
value as well as the envelope. Their results record `prior_value`,
`automation_state` and `start_settled`; retain those complete replies. A write
with active or overridden automation is refused before seeding or clearing.
The `automation` case writes the beat-zero ramp first on a fresh, inactive
parameter, verifies the sampled staircase, and checks that one Undo restores
both its previous manual value and missing envelope. Redo restores the ramp.
It then replaces that ramp with points starting at beat **0.25**, which avoids
seeding and is permitted over active automation. Explicit positive durations,
invalid-request preservation and another Undo/Redo are checked against the
correct ramp/points snapshots.

The separate `automation-points-zero` case must run after reopening the pristine
fixture. It checks beat-zero **points** mode, its sampled values and the same
single-Undo recovery of the prior manual value and missing envelope. It does
not silently omit points-mode beat-zero coverage. If the initial fixture state
is nonzero, report the case **blocked**. Inspect stopped clips and the
Session/Arrangement automation in Live; stopping, unlinking or re-enabling
may be necessary, but do not remove the guard or call a rejected write a pass.
The automated tests simulate Live and do not establish this actual Live behavior.

Batch preflight rejects **all beat-zero envelope writes**, including points
and ramps, because they require a wait across Live ticks. Use standalone calls
for those writes. A batch automation write must start strictly after beat zero;
the supplied batch case uses name/pan edits and does not bypass this restriction.

Undo and Redo act on Live's current history, so send each only once for the
intended assertion. The runner does not retry either action. A timeout or lost
response does not justify a second Undo: inspect the envelope, name and pan
first, retain the error and stop the case if the state is uncertain.

## 6. Inspect the actual Live result

The SDK checks are necessary but do not establish audible or visual correctness.
Keep screenshots and a short written result for these observations:

- All four selected Utility `Gain`/`Output` controls display approximately **-12 dB** for the finite
  display-string request. The `-inf dB` test reaches the mute endpoint. Native
  numeric values and their numeric-string equivalents produce the same readback.
  Invalid strings leave the value unchanged; inspect the UI while that call runs.
- Session clip envelope steps remain low before beat 2 and high after beat 2.
  The ramp has visible finite-width stairs, reaches the final value, and holds it
  through the end. Readback samples around boundaries use epsilon beats to avoid
  confusing an exact event boundary with a vanished zero-duration segment.
- On each fresh beat-zero case, one Undo removes the new envelope and restores
  the prior manual value; Redo restores it. In `automation`, the later points
  replacement's Undo restores the ramp and Redo restores the points. There must
  be no unrelated note/clip loss. The batch's single Undo restores both its name
  and pan, while its runtime failure reports the completed first edit.
- During cancellation, recording starts before cancellation is sent; afterwards
  transport is stopped, Arrangement recording is off, no track remains armed,
  the owned temporary Bounce track is removed, and original tracks survive.
  The recording call must return an explicit cancellation/error; a successful
  completed recording cannot count as cancellation acceptance.
  Audio files may remain in the disposable project's recording directory. A
  successful next edit confirms that the edit queue recovered.

Also run a short successful `record_section` (for example beats 0–4) on a
**separate disposable arrangement containing an audible stock-device MIDI clip**.
Keep the same device/name fixture, add audible arrangement material at beats
0–4 in the UI, and run:

```powershell
& $Py -I "$Root\source\scripts\test-live-candidate.py" record 2>&1 | Tee-Object "$Root\evidence-python\record.log"
```

The runner checks `transport_recovery.confirmed_stopped`, the returned WAV path
and `cleanup_errors`; open/play that WAV and confirm it contains the expected sound.
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
