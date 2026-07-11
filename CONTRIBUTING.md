# Contributing

Bug reports and pull requests are welcome.

## Development setup

```bash
git clone https://github.com/wstierhout/ableton-live-mcp
cd ableton-live-mcp
uv run --extra dev pytest      # run the tests
uvx ruff check .               # lint
```

The tests do not need Ableton running. They cover the socket protocol, the command
dispatch tables, and tool registration.

## Testing against Ableton

Changes to `ableton_live_mcp/remote_script/__init__.py` only take effect after copying
the file into your User Library Remote Scripts folder (`ableton-live-mcp install` does
this) and restarting Live, since Live scans Remote Scripts at startup.

## Notes

- Keep new tool docstrings specific about units, ranges, and side effects. The
  docstrings are what an AI reads to call the tool correctly.
- Every command a tool sends must be handled by the Remote Script. A test enforces
  this.
