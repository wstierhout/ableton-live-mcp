# Changelog

## 1.1.1

- `install`, `uninstall`, `doctor`, and `--version` no longer load the full tool
  surface, so they run faster and still work if the server dependencies are not
  importable.
- The Remote Script port is now configurable with `ABLETON_MCP_PORT` (set the
  client's `ABLETON_PORT` to match) for hosts where 9877 is taken.

## 1.1.0

- The Remote Script now ships inside the package, so `ableton-live-mcp install`
  copies it from the local install with no network access and always matches the
  server version.
- Cleaner internal layout: the Python package is `ableton_live_mcp` and the
  Live-side script lives at `ableton_live_mcp/remote_script/`.
- Published to the official MCP Registry as `io.github.wstierhout/ableton-live-mcp`.

## 1.0.1

Packaging for PyPI. Published to PyPI as `mcp-server-ableton-live`. The run command is
`ableton-live-mcp`.

## 1.0.0

First public release as a standalone project.

- 104 tools covering session and transport, tracks and mixer, clips and MIDI
  notes, devices and rack chains, the browser, the arrangement, and a set of
  server-side generators (drum patterns, chord progressions, basslines, ASCII
  drum grids, one-call session setup).
- Full mixer control: volume, pan, mute, solo, arm, sends, master volume, and
  crossfader.
- Device and rack-chain parameter control, including devices on the Master and
  Return tracks, so an agent can set up mastering and shared effects in the box.
- Clip automation envelopes, Groove Pool swing, quantize with strength, and note
  probability.
- Routing and monitoring, track and master meters, scene lifecycle, locators,
  record modes, and fixed-length session capture.
- `batch_commands` runs several edits in one round trip and one undo step.
- Server instructions and two workflow prompts (`make_a_beat`, `mix_and_master`)
  so the model learns the conventions before its first call.
- The server makes no network calls of its own.
- Tests and CI (lint, Remote Script byte-compile, pytest on 3.10 and 3.12).
- Verified against Ableton Live 12.4 with a full command sweep.
