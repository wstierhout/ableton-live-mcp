# Changelog

## 1.4.0

- `get_session_snapshot`: the whole set (tempo, time signature, play state, and
  every track's name, type, mute/solo/arm, volume, clip count, and devices) in one
  call, instead of many per-track requests.
- `set_clip_warp`: toggle warping and choose the warp algorithm on audio clips.
- `set_simpler_playback_mode`: switch a Simpler between Classic, One-Shot, and
  Slicing (sample chopping).
- 127 tools. New tools verified against Ableton Live 12.4.

## 1.3.0

- Deeper generators (all seedable, pure music theory): `generate_euclidean_drums`
  (Bjorklund), `generate_voiced_progression` (rootless, quartal, shell, block jazz
  voicings with voice-leading), `generate_melody` (nearest-pitch voice-leading,
  chromatic approach notes, phrase arc), `generate_walking_bass`, `generate_groove`
  (pocket micro-timing, ghost notes), `generate_genre_progression` (39-genre chord
  table), and `humanize_clip`. Progressions accept space-, comma-, or dash-separated
  chords.
- New Live-side tools: `tap_tempo`, `set_groove_amount`, `set_swing_amount`,
  `jump_by`, `jump_to_cue`, `set_ableton_link`, `delete_device`, `create_take_lane`.
- 124 tools total. All new tools verified against Ableton Live 12.

## 1.2.0

- New `offline` toolset: analyze saved `.als` project files with Live closed.
  `als_summary`, `als_list_tracks`, `als_extract_midi`, `als_diff` (compare two
  versions), and `als_find_unfinished` (lint for missing instruments, empty or
  muted tracks, no master limiter). Pure standard library, no Live connection.
- 109 tools total.

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
