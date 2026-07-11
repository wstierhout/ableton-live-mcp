<!-- mcp-name: io.github.wstierhout/ableton-live-mcp -->

# Ableton Live MCP Server

[![CI](https://github.com/wstierhout/ableton-live-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/wstierhout/ableton-live-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/wstierhout/ableton-live-mcp)](https://github.com/wstierhout/ableton-live-mcp/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)

Control Ableton Live from an AI assistant. This is a Model Context Protocol (MCP)
server that gives Claude, Cursor, Codex, or any MCP client 104 tools for building
tracks, editing MIDI, loading instruments and effects, mixing, and mastering inside
a running Ableton Live set.

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
| Session and transport | session info, play/stop, tempo, time signature, loop, locators, scenes, undo/redo, capture MIDI, song scale |
| Tracks and mixer | create and delete MIDI, audio, and return tracks; volume, pan, mute, solo, arm, sends; input/output routing; meters |
| Clips and notes | create clips, write and edit MIDI notes (with probability), quantize with strength, Groove Pool swing, loop, gain, pitch, warp |
| Devices | browse and search by name, load instruments and effects onto any track including Master and Returns, read and set any device or rack-chain parameter, per-pad drum control |
| Arrangement | place clips on the timeline, read and delete arrangement clips, write clip automation |
| Generators | drum patterns in 7 styles, chord progressions, basslines, ASCII drum grids, one-call session setup |
| Batch | run many edits in one round trip and one undo step |

There are also two workflow prompts (`make_a_beat`, `mix_and_master`) and a set of
server instructions that teach the model the conventions before its first call:
0-based indices, beats for time, 0.85 volume equals 0 dB, and native ranges for
device parameters.

## Notes

Every action is a specific tool with validated arguments; there is no arbitrary-code
path. Some tools still make destructive edits (delete a track, replace a clip's
notes, overwrite an arrangement region), so save your work before a big session.

## Focusing the toolset

The server registers 104 tools. That is a lot for a model to choose from on a small
task. Set `ABLETON_TOOLSETS` to load only the groups you need, for example
`ABLETON_TOOLSETS=session,tracks,clips,generators`. Groups: `session`, `tracks`,
`clips`, `devices`, `browser`, `arrangement`, `generators`. Unset loads everything.

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

## Credits and license

MIT licensed, maintained by [Wouter Stierhout](https://github.com/wstierhout).
See `LICENSE`. Not affiliated with Ableton.
