# Contributing

Bug reports and pull requests are welcome.

## Development setup

```bash
git clone https://github.com/wstierhout/ableton-live-mcp
cd ableton-live-mcp
uv run --extra dev pytest      # run the tests
uvx ruff check .               # lint
```

Tests run without Ableton. They exercise real socket/MCP boundaries, numerical
results and bridge handlers with simulated Live objects. They do not certify Live
compatibility, musical quality, or a successful real recording.

Keep tests focused on observable behavior or a concrete failure. Expected results
must come from a requirement, known example or independent invariant. Check that
a regression test fails when the relevant behavior is broken. Avoid exact private
IDs, source formatting, incidental call sequences and duplicated scenarios. Do not
change an expectation just to make a failure pass; first decide whether the intended
behavior changed. No test-count or coverage-percentage target is required.

Before release, run `uv run python scripts/export_tool_catalog.py --check`. Export
the catalog with `--output /tmp/ableton-tools.json` to review names, parameter
descriptions, units and defaults. Release metadata checks are separate from the
functional test suite.

## Testing against Ableton

Changes to `ableton_live_mcp/remote_script/__init__.py` only take effect after copying
the file into your User Library Remote Scripts folder (`ableton-live-mcp install` does
this) and restarting Live, since Live scans Remote Scripts at startup.

Use a disposable set and record the exact server, bridge and Live versions. Check
note replacement and volume readback, instrument loading, batches/Undo, transport,
recording cancellation and manual seeks, and browser results in a larger library.
Check that malformed replacements preserve existing notes/envelopes, missing kits
leave devices untouched, and failures after an edit disclose what already completed.
Verify that a reported recording file exists and contains the intended audio.
Audition generated music, including voice leading; a random-leap comparison is
not a musical acceptance test. Run the offline rack tools on an actual local
factory `.adg` file as well; there is no developer-specific fixture path in CI.

## Notes

- Keep new tool docstrings specific about units, ranges, and side effects. The
  docstrings are what an AI reads to call the tool correctly.
- Keep server and bridge command contracts aligned. Tests exercise core workflows
  and shared vocabularies; they are not exhaustive coverage of every Live command.
