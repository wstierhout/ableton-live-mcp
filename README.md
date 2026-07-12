<!-- mcp-name: io.github.wstierhout/ableton-live-mcp -->

# Ableton Live MCP Server

[![CI](https://github.com/wstierhout/ableton-live-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/wstierhout/ableton-live-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/wstierhout/ableton-live-mcp)](https://github.com/wstierhout/ableton-live-mcp/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![Glama tool quality](https://glama.ai/mcp/servers/wstierhout/ableton-live-mcp/badges/score.svg)](https://glama.ai/mcp/servers/wstierhout/ableton-live-mcp)

Control Ableton Live from an AI assistant. This is a Model Context Protocol (MCP)
server that gives Claude, Cursor, Codex, or any MCP client 154 tools for building
tracks, editing MIDI, loading instruments and effects, mixing, and mastering inside
a running Ableton Live set, plus offline tools that read and diff saved `.als`
project files with Live closed.

A Remote Script runs inside Live and exposes the Live API over a local socket. The
MCP server turns that into typed, validated tools that an AI can call correctly,
with per-tool documentation, read-only and destructive hints, and workflow prompts.

## Quickstart

You need Ableton Live 11 or 12, Python 3.10+, and [uv](https://docs.astral.sh/uv/)
(`brew install uv`).

**1. Register the MCP server.**

Claude Code:

```bash
claude mcp add AbletonMCP -s user -- uvx mcp-server-ableton-live
```

Claude Desktop or Cursor, in the `mcpServers` block of the config file:

```json
{
  "mcpServers": {
    "AbletonMCP": {
      "command": "uvx",
      "args": ["mcp-server-ableton-live"]
    }
  }
}
```

(To run an unreleased revision instead, use
`uvx --from git+https://github.com/wstierhout/ableton-live-mcp ableton-live-mcp`.)

**2. Install the Remote Script.** This copies the Live-side script into your User
Library:

```bash
uvx mcp-server-ableton-live install
```

Then restart Live and set Settings > Link/Tempo/MIDI > Control Surface to `AbletonMCP`
(Input and Output: None). You only do this once. See Ableton's
[guide to third-party Remote Scripts](https://help.ableton.com/hc/en-us/articles/209072009-Installing-third-party-remote-scripts)
if the entry does not appear.

**3. Check the setup.**

```bash
uvx mcp-server-ableton-live doctor
```

**4. Ask for music.** For example: "Make a lofi beat at 80 BPM with a dusty drum kit,
an upright bass, and Rhodes chords, then put a limiter on the master at -1 dB."

## What it can do

| Area | Tools |
|---|---|
| Session and transport | session info, one-call session snapshot, play/stop, tempo, tap tempo, time signature, loop, locators and cue navigation, scenes, undo/redo, capture MIDI, song scale, global groove and swing, Ableton Link |
| Tracks and mixer | create and delete MIDI, audio, and return tracks; delete devices; take lanes; group-track fold; volume, pan, mute, solo, arm, sends; input/output routing; meters |
| Clips and notes | create clips, write and edit MIDI notes (with probability), quantize with strength, Groove Pool swing, loop, gain, pitch, warp mode |
| Devices | browse and search by name, load instruments and effects onto any track including Master and Returns, read and set any parameter, sidechain routing, sample preview, rack macro variations, Simpler sample slicing, per-pad drum control, curated device knowledge base |
| Arrangement | place clips on the timeline, read and delete arrangement clips, write clip automation |
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

## Focusing the toolset

The server registers 154 tools. That is a lot for a model to choose from on a small
task. Set `ABLETON_TOOLSETS` to load only the groups you need, for example
`ABLETON_TOOLSETS=session,tracks,clips,generators`. Groups (each may span several modules): `session`, `tracks`, `clips`, `devices`,
`browser`, `arrangement`, `generators`, `audio`, `analysis`, `offline`, `recipes`.
Unset loads everything.

## Conventions

- Indices are 0-based. Times and lengths are in beats. Volume uses Live's 0.0 to 1.0
  fader range where 0.85 is 0 dB.
- Device parameters use each parameter's own range. Read `get_device_parameters`
  first and check min, max, and the display value before setting.
- `add_notes_to_clip` replaces the clip's notes. Use `edit_notes` to add or remove a
  few without touching the rest.
- Placing a session clip into the arrangement overwrites whatever is under it, which
  is also how you replace a section.
- Transport replies report the state before the command ran. Confirm with
  `get_session_info`.

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

CI runs lint, a byte-compile of the Remote Script, and the tests on Python 3.10 and
3.12.

## Frequently asked questions

### What is the Ableton Live MCP Server?

It is a local Model Context Protocol server that lets AI clients such as Claude,
Cursor, and Codex inspect and control Ableton Live through typed tools. It can build
tracks, edit MIDI, load and configure devices, arrange clips, mix a set, and analyze
saved `.als` projects.

### How is this different from other Ableton MCP servers?

This server exposes 154 specific, validated tools rather than an arbitrary-code
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
See `LICENSE`. Not affiliated with Ableton.
