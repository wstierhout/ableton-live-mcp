---
name: ableton-workflow
description: Use AbletonMCP to inspect or edit Ableton Live sessions, analyze saved .als projects, or troubleshoot this local bridge. Use for musical work in Live, not general audio questions or other DAWs.
---

# AbletonMCP workflow

This community plugin runs `mcp-server-ableton-live` locally. It is not an official
Ableton or OpenAI product. Tool data can still reach the user's AI provider.

## Connect and orient

Use the plugin's available Ableton tools. Do not silently switch to a similarly
named server or install another bridge. If tools are missing, inspect the MCP
connection and suggest the setup guide at https://abletonmcp.com/docs/codex/.

For live work, read `get_session_info` and relevant track/device state first.
For saved-file analysis, use `als_summary`, `als_lint` or `als_diff`; Live can stay
closed. Ask for the file if its path is not provided. Never upload the set yourself.

Setup is separate from tool use: `uvx mcp-server-ableton-live@1.8.1 install` copies
the Remote Script; it must not appear in the MCP launch arguments. A custom User
Library uses `--user-library`. An existing different script requires review and
explicit `--replace`, which keeps a backup. Restart Live, select AbletonMCP as
Control Surface with Input/Output None, then run the same version's `doctor --json`.
Do not auto-replace a bridge, change client config or enable a second port-9877 server.

## Make the requested musical change

- Indices are zero-based and can shift after deletion; reread targets afterwards.
- Musical time is beats, not seconds. Faders are normalized, not dB; 0.85 is unity.
- Read device parameter ranges/display first. Numbers use native values; strings
  such as `250 Hz` request a bounded display conversion, which can refuse unsupported
  mappings. Read the returned display and warnings; do not invent a conversion curve.
- `add_notes_to_clip` replaces all notes. Use `edit_notes` for incremental edits.
- `duplicate_to_arrangement` overwrites the overlapping destination. Confirm the
  target/range for replacements or deletions; prefer a disposable set for exploration.
- Use `batch_commands` for an approved related edit when useful for a single undo
  step. An uncertain timeout is not permission to replay a mutating command: inspect
  current state first. Modal dialogs in Live can block the script.
- Preserve user instrument/style choices. Auditioning, starting playback or recording
  must fit the user's task; an analysis-only request should remain read-only.

## Verify and report

Read the changed clips, parameters or session state after writing. Transport replies
can describe the prior state. `session_diff` compares successive snapshots; establish
a baseline before the edit. Report the observed result and any unverified part.

`analyze_mix` is a session-state heuristic, not measured audio. `analyze_audio_file`
measures local WAV (integer/float), AIFF, FLAC, Ogg, MP3, AAC and M4A sample peak/RMS
after decoding for a bounded interval, not LUFS, true peak,
mastering quality or the full file when `partial` is true. Never derive LUFS from a
fader or peak meter. `record_section` is a capture workflow, not Live's final export.

`replace_simpler_sample` is capability-gated for Live 12.4+. Do not bypass an
unsupported-feature error with arbitrary Python execution or an SDK migration.
