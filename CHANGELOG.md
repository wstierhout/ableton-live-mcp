# Changelog

## 1.7.1

- Internal cleanup (/simplify): shared the tolerant `.als`/`.adg` XML readers,
  rooted the offline mixer/automation lookups so they no longer scan whole tracks,
  and `adg_summary`/`adg_edition` skip the drum-pad/macro-mapping walks that only
  `adg_analyze` needs.
- Toolset groups are now domains that can span modules (e.g. `generators` covers the
  basic, advanced, and motif generators; `offline` covers `.als` and `.adg`), from a
  single source, so `ABLETON_TOOLSETS` has 11 groups instead of one per module.
- Deduped note/diff/clip-pool helpers across the new modules.

## 1.7.0

- Browser sample preview: `preview_browser_item` / `stop_browser_preview` audition a
  sample or preset without loading it onto a track.
- `als_details`: read locators and per-track fader volume, pan, and automation-lane
  count from a saved `.als`; `als_diff` now reports volume/pan/automation changes
  between versions.
- `get_scale_info`: read the song scale, root, intervals, and tuning system.
- Three more guided prompts: `start_a_track`, `sound_design`, `analyze_and_improve`.
- 153 tools.

## 1.6.0

- Key/scale detection (Krumhansl-Kessler): `detect_clip_key`, `detect_track_key`,
  `detect_session_key`, and offline `als_detect_key` return tonic, mode, and
  confidence, so a detected key can feed the generators.
- `record_section`: bounce a section of the arrangement to a WAV without the Export
  dialog (arms a resampling/routed audio track and records in real time), so an
  agent can analyze an internal signal.
- Sidechain routing: `get_device_routing` / `set_device_routing` set a compressor's
  sidechain source over the Live API.
- Motif transforms (`transform_clip`: invert, retrograde, augment, transpose) and
  minimalist generators (`generate_phase`, `generate_additive`).
- Offline `.adg`/`.adv` rack and preset parser with Suite/Standard edition detection
  (`adg_summary`, `adg_analyze`, `adg_edition`).
- `session_diff` (what changed since the last call), `describe_device` /
  `list_known_devices` (device knowledge base with linear-vs-curve unit taxonomy),
  `get_group_info` / `set_fold_state` (group tracks), and `save_set` (which confirms
  the Live API cannot save on 12.4, so Save stays GUI-only).
- 18 more genre progressions.
- 149 tools. New tools verified against Ableton Live 12.4.

## 1.5.0

- `analyze_mix`: scan the live set for likely mix problems (several tracks with no
  headroom, muted or empty tracks, a MIDI track with no instrument, nothing
  playing).
- `apply_recipe` / `list_recipes`: scaffold a genre starter (lofi beat, house
  groove) in one call: set the tempo, add Drums/Bass/Chords tracks, try to load
  fitting instruments, and write generated parts.
- `describe_capabilities`: a high-level map of the tool groups and conventions for
  agent orientation.
- Removed `set_clip_warp` as redundant: `set_clip_audio` already sets `warping`
  and `warp_mode`.
- 130 tools.

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
