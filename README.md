<!-- mcp-name: io.github.wstierhout/ableton-live-mcp -->

# Ableton Live MCP Server

[![CI](https://github.com/wstierhout/ableton-live-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/wstierhout/ableton-live-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/wstierhout/ableton-live-mcp)](https://github.com/wstierhout/ableton-live-mcp/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![Glama tool quality](https://glama.ai/mcp/servers/wstierhout/ableton-live-mcp/badges/score.svg)](https://glama.ai/mcp/servers/wstierhout/ableton-live-mcp)

**Website and installation guide: [abletonmcp.com](https://abletonmcp.com)**

Control Ableton Live from an AI assistant. This is a Model Context Protocol (MCP)
server that gives Claude, Cursor, Codex, or any MCP client 157 tools for building
tracks, editing MIDI, loading instruments and effects, mixing, and mastering inside
a running Ableton Live set, plus offline tools that read and diff saved `.als`
project files with Live closed.

A Remote Script runs inside Live and exposes the Live API over a local socket. The
MCP server turns that into typed, validated tools that an AI can call correctly,
with per-tool documentation, read-only and destructive hints, and workflow prompts.

## 2.0 release candidate

This checkout is **2.0.0rc1**, an unpublished candidate with 157 tools. It includes
clip automation duration/ramp fixes and reading, plus combined parameter conversion
fixes from [#4](https://github.com/wstierhout/ableton-live-mcp/pull/4),
[#5](https://github.com/wstierhout/ableton-live-mcp/pull/5),
[#6](https://github.com/wstierhout/ableton-live-mcp/pull/6), and
[#7](https://github.com/wstierhout/ableton-live-mcp/pull/7).
Arrangement automation from #8 is deferred.

Read the [2.0 migration notes](docs/MIGRATION-2.0.md) before upgrading clients or
custom integrations. The version-pinned PyPI commands below apply only after this
prerelease is published; testing an unpublished candidate requires the matching
source checkout or supplied wheel; see the
[Windows candidate testing guide](docs/WINDOWS-CANDIDATE-TESTING.md). Automated
checks and real Live acceptance are separate release gates; this notice does not
claim either has passed.

## Quickstart

You need Ableton Live 11 or 12, Python 3.10+, and [uv](https://docs.astral.sh/uv/)
(`brew install uv`).

**1. Register the MCP server.**

Codex (local desktop, CLI or IDE host):

```bash
codex mcp add ableton -- uvx mcp-server-ableton-live@2.0.0rc1
codex mcp list
```

For manual configuration and troubleshooting, see the [Codex guide](https://abletonmcp.com/docs/codex/).

Claude Code:

```bash
claude mcp add AbletonMCP -s user -- uvx mcp-server-ableton-live@2.0.0rc1
```

Claude Desktop or Cursor, in the `mcpServers` block of the config file:

```json
{
  "mcpServers": {
    "AbletonMCP": {
      "command": "uvx",
      "args": ["mcp-server-ableton-live@2.0.0rc1"]
    }
  }
}
```

To test an unpublished revision, install the supplied candidate wheel or use the
exact candidate checkout. A Git URL without a commit selects the repository's
current default branch and may launch an older release instead.

**2. Install the Remote Script.** This copies the Live-side script into your User
Library:

```bash
uvx mcp-server-ableton-live@2.0.0rc1 install
```

Then activate the bridge in Live:

1. Fully restart Ableton Live.
2. Open **Settings > Tempo & MIDI**, then find the **MIDI** section and its
   **Control Surface** list.
3. In an unused **Control Surface** row, select **AbletonMCP**. Leave existing
   hardware controller rows unchanged.
4. In that same row, set **Input: None** and **Output: None**.

Link is a separate Settings page in current Live versions. In older versions, look
under **Link, Tempo & MIDI** (or **Link/MIDI**) in Settings/Preferences, then find
the MIDI section.

This selection is required for every MCP client. Installation copies
the Remote Script; selecting the Control Surface starts it inside Live. The bridge
uses a local socket, so its row does not need hardware MIDI input/output ports.

Run the installer again after upgrading the package so
the Live-side script stays version-matched. See Ableton's
[guide to third-party Remote Scripts](https://help.ableton.com/hc/en-us/articles/209072009-Installing-third-party-remote-scripts)
if the entry does not appear.

**3. Check the setup.**

```bash
uvx mcp-server-ableton-live@2.0.0rc1 doctor --json
```

Use the same `--user-library "/path/to/User Library"` for install and doctor if your
library has moved. If an older/different script exists, installation stops: review
it before adding `--replace`. A unique backup is retained. Restart Live before
diagnosing; doctor checks both installed bytes and the running bridge version.

**4. Start read-only.** Ask for the tempo and track names without changes. Then,
in a disposable set, try: "Make a lofi beat at 80 BPM with a dusty drum kit,
an upright bass, and Rhodes chords, then put a limiter on the master at -1 dB."

## What it can do

| Area | Tools |
|---|---|
| Session and transport | session info, one-call session snapshot, play/stop, tempo, tap tempo, time signature, loop, locators and cue navigation, scenes, undo/redo, capture MIDI, song scale, global groove and swing, Ableton Link |
| Tracks and mixer | create and delete MIDI, audio, and return tracks; delete devices; take lanes; group-track fold; volume, pan, mute, solo, arm, sends; input/output routing; meters |
| Clips and notes | create clips, write and edit MIDI notes (with probability), quantize with strength, Groove Pool swing, loop, gain, pitch, warp mode |
| Devices | browse and search by name, load instruments and effects onto any track including Master and Returns, read and set any parameter, sidechain routing, sample preview, rack macro variations, Simpler sample slicing, per-pad drum control, curated device knowledge base |
| Arrangement | place clips on the timeline, read and delete arrangement clips, write and read clip automation |
| Generators | drum patterns in 7 styles, euclidean rhythms, chord progressions, voice-led jazz voicings, 50-plus genre-aware progressions, voice-leading melodies, walking basslines, motif transforms (invert, retrograde, augment), minimalist processes, humanize, one-call session setup |
| Batch | run many edits in one round trip and one undo step |
| Audio and analysis | record a section to a WAV without the Export dialog, detect key and scale, scan the mix for problems, diff the session since the last check |
| Offline (no Live) | summarize, list tracks, extract MIDI, detect key, read mixer and locator detail, diff two versions, lint a saved `.als`, and parse `.adg`/`.adv` racks with edition detection |
| Recipes | scaffold a genre starter (lofi, house) in one call |

There are also five workflow prompts (`make_a_beat`, `mix_and_master`, `start_a_track`,
`sound_design`, `analyze_and_improve`) and a set of server instructions that teach the
model the conventions before its first call:
0-based indices, beats for time, 0.85 volume equals 0 dB, and native ranges for
device parameters.

## Notes

Every action is a specific tool with validated arguments; there is no arbitrary-code
path. Some tools still make destructive edits (delete a track, replace a clip's
notes, overwrite an arrangement region), so save your work before a big session.

## Audio analysis and session changes

`analyze_audio_file` reads WAV (integer and floating point), AIFF/AIFF-C, FLAC,
Ogg Vorbis, MP3, AAC and M4A/MP4 (AAC or Apple Lossless) locally. The package installs
the PyAV decoder dependency. It preserves the file, sample rate, channel layout and
floating-point headroom; compressed files are measured after decoding. Analysis
covers the first 30 seconds by default (up to 120 seconds, with a 32-million-sample
processing budget), and reports when the result is partial. Measurements include
sample peak and RMS, not LUFS or true peak.

`session_diff` distinguishes renames, reordering and duplicate track names using
identifiers from the running bridge. Call it before and after an edit; the first
call establishes the comparison baseline. Installing an updated Remote Script and
restarting Live keeps the bridge's snapshot format matched to the server.

## Focusing the toolset

The server registers 157 tools. That is a lot for a model to choose from on a small
task. Set `ABLETON_TOOLSETS` to load only the groups you need, for example
`ABLETON_TOOLSETS=session,tracks,clips,generators`. Groups (each may span several modules): `session`, `tracks`, `clips`, `devices`,
`browser`, `arrangement`, `generators`, `audio`, `analysis`, `offline`, `recipes`.
Unset loads everything.

## Conventions

- Indices are 0-based. Times and lengths are in beats. Volume uses Live's 0.0 to 1.0
  fader range where 0.85 is 0 dB.
- Device parameters use each parameter's own range. Read `get_device_parameters`
  first and check min, max, and the display value before setting. Numeric strings
  such as `"0.5"` use native units for continuous parameters. Quantized parameters
  match exact display labels first, including numeric labels: `"8"` can select the
  label at native index 7, while the number `8` selects native index 8. The result
  warns when that label match changes the numeric interpretation. Send an actual
  number to choose by native position. Display strings such as `"-12 dB"` request
  a supported display mapping. Top-level and rack setters share these rules.
- `write_automation` replaces a single Session clip parameter envelope. A point
  holds until the next point or clip end unless a positive duration is supplied.
  Linear ramps become bounded stepped envelopes (at most 4,096 steps).
  Quantized parameters require integer point values and do not support ramp mode.
  A write starting at beat zero sets the manual parameter value before its deferred
  envelope write, and refuses a nonzero automation state before changing anything.
  Beat-zero writes must be standalone calls, outside batches. Inspect the returned
  prior value, automation state and `start_settled` readback.
  `read_automation` returns at most 1,024 samples at explicit beat positions or on
  a start/end/step grid. A grid includes its end only when the end is on the grid.
  These are Live's sampled values, not original control points; sample just after
  step boundaries when checking a transition. These are clip envelopes.
- `add_notes_to_clip` replaces the clip's notes. Use `edit_notes` to add or remove a
  few without touching the rest.
- Placing a session clip into the arrangement overwrites whatever is under it, which
  is also how you replace a section.
- Start, stop and seek replies include `requested`, `observed`, and `confirmed` after a
  later Live tick. Continue dependent work only when `confirmed=true`.
- Batches preflight all commands before editing, contain at most 128 commands,
  and stop on runtime errors with completed-step details. They do not roll back
  automatically. Transport, locators, save, undo/redo, deferred scans and composite
  tools require standalone calls. Locators require stopped playback for exact placement.
- Tool replies retain text and expose JSON under `structuredContent.data`.
  Failures have `isError=true` and `structuredContent.error`, including partial
  results and an operation ID when available. `get_session_info(operation_id=...)`
  inspects the last 256 mutation receipts; reads do not evict them. An expired
  receipt is not proof an edit failed.
- Cancellation prevents edits that have not started. A started multi-command edit
  finishes its workflow; recording has explicit cancellation and recovery points.
  Shutdown waits for those workers before closing the connection.
- `record_section` requires stopped transport and other tracks disarmed. It
  rejects an Arrangement loop that would wrap during capture. It
  disarms its own track on completion or cancellation, uses stable track IDs for
  cleanup, and reports whether transport recovery confirmed a stop. Cleanup does
  not rewind the playhead, so it preserves intervening user seeks. It is a real-time
  capture, not a sample-accurate offline render.
- Browser and Session-note scans continue across Live ticks within bounded
  budgets. Check `partial` and `limit_reasons` before treating a missing match or
  key estimate as complete coverage.

The reliability updates require bridge protocol 2. After updating the server,
run `ableton-live-mcp install` (review `--replace` when the installer reports a
different existing script) and restart/reload the Control Surface. `uv` remains
the installation and launch tool. Audio calculations use an optional native
module supplied in platform wheels; a universal Python wheel provides a
compiler-free fallback where no native wheel matches. Source builds can select
that fallback with `ABLETON_MCP_PURE_PYTHON=1`. The Live-side Remote Script remains
Python 3.7 compatible.

## Troubleshooting

- `AbletonMCP` is missing from the Control Surface list: the folder has to be named
  exactly `AbletonMCP`, and Live only scans scripts at startup, so restart it.
- Every command times out: a dialog is open in Live and it blocks the script. On the
  trial, that is the startup nag. Dismiss it and retry.
- Port 9877 is not listening: Live is not running, or the Control Surface is not
  set. If another program already uses 9877, set `ABLETON_MCP_PORT` in Live's
  launch environment and `ABLETON_PORT` for the server so both sides match.
- You cannot export or freeze from a tool: Live's API has no render or freeze
  function, so those stay manual. For rendered audio, export with the shortcut and
  analyse the file separately.

## Security

The Remote Script listens on `127.0.0.1:9877` with no authentication. It is bound to
loopback so only local processes can reach it. Do not forward that port or change the
bind address to a public interface. See [SECURITY.md](SECURITY.md).

## Development

```
ableton_live_mcp/
  server.py        entrypoint: runs the MCP server or a setup subcommand
  app.py           FastMCP app, lifecycle, and server instructions
  connection.py    socket client to the Remote Script
  cli.py           install / uninstall / doctor subcommands
  tools/           session, tracks, clips, devices, browser, arrangement,
                   generators, prompts
  remote_script/
    __init__.py    the Live-side script and its command dispatch tables
tests/             protocol, dispatch-contract, and registration tests
```

```bash
uv run --extra dev pytest      # tests
uvx ruff check .               # lint
```

CI defines lint, a byte-compile of the Remote Script, and the full offline suite
on Ubuntu with Python 3.10, 3.12, 3.13 and 3.14, plus Python 3.13 on Windows and
macOS. A separate Windows job verifies the installed portable wheel. Native audio
wheels have their own platform build/parity jobs; actual Live acceptance remains
separate.

## Frequently asked questions

### What is the Ableton Live MCP Server?

It is a local Model Context Protocol server that lets AI clients such as Claude,
Cursor, and Codex inspect and control Ableton Live through typed tools. It can build
tracks, edit MIDI, load and configure devices, arrange clips, mix a set, and analyze
saved `.als` projects.

### How is this different from other Ableton MCP servers?

This server exposes 156 specific, validated tools rather than an arbitrary-code
execution tool. It includes destructive/read-only hints, workflow prompts, built-in
music generators, mixing and analysis tools, and offline `.als`/`.adg` inspection.
The Remote Script and MCP package are versioned and tested together.

### Can it work while Ableton Live is closed?

The real-time control tools require Ableton Live and the bundled Remote Script to be
running. The offline tools can summarize, inspect, lint, extract MIDI from, and diff
saved `.als`, `.adg`, and `.adv` files without launching Live.

### Which MCP clients are supported?

Any client that can launch a local stdio MCP server can use it, including Claude
Desktop, Claude Code, Cursor, Codex, and compatible IDEs. The canonical command is
`uvx mcp-server-ableton-live`.

### Is this an official Ableton product?

No. It is an independent, open-source integration and is not affiliated with or
endorsed by Ableton.

## Credits and license

MIT licensed, maintained by [Wouter Stierhout](https://github.com/wstierhout).
See `LICENSE`. Ableton and Live are trademarks of Ableton AG. This independent
open-source project is not affiliated with or endorsed by Ableton AG.
