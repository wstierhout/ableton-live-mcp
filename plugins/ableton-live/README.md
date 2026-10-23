# AbletonMCP community plugin for Codex

Independently maintained by Wouter Stierhout. Not affiliated with or endorsed by
OpenAI or Ableton. This local stdio plugin is not a hosted ChatGPT web connector.

The plugin bundles a pinned MCP connection and an Ableton workflow skill. It does
not silently install the Remote Script, add account configuration, or start Live.

## Requirements and setup

Install uv. After **mcp-server-ableton-live 2.0.0rc1 has been published**:

```sh
uvx mcp-server-ableton-live@2.0.0rc1 install
```

**Required: activate the bridge in Ableton Live.** Installing the Codex plugin does
not configure this Live setting for you:

1. Fully restart Live.
2. Open **Settings > Tempo & MIDI**, then find the **MIDI** section and its
   **Control Surface** list.
3. In an unused **Control Surface** row, select **AbletonMCP**. Leave existing
   hardware controller rows unchanged.
4. In that same row, set **Input: None** and **Output: None**. This bridge uses a
   local socket and does not need hardware MIDI ports.

Link is a separate Settings page in current Live versions. In older versions, look
under **Link, Tempo & MIDI** (or **Link/MIDI**) in Settings/Preferences, then find
the MIDI section.

With Live running, check the connection:

```sh
uvx mcp-server-ableton-live@2.0.0rc1 doctor --json
```

Look for `"ok": true`. Relocated libraries accept `--user-library "/path/to/User Library"`
for both install and doctor.
Review existing scripts before opting into `install --replace`.

To test this source folder before public distribution, ask Codex's built-in
`$plugin-creator` to register this folder in your personal marketplace. Review and
approve the local configuration change, install from that source, then start a
fresh chat. Do not enable this plugin and a manually configured identical server
at the same time. Disabling the plugin leaves your music and Remote Script intact.

For direct MCP setup without installing the plugin:

```sh
codex mcp add ableton -- uvx mcp-server-ableton-live@2.0.0rc1
codex mcp list
```

Start with: “Check the connection and list the tempo and tracks. Do not change anything.”

## Release validation

The manifest/skill validators check package structure, not a real Codex installation
or a running Live session. Those smoke tests remain explicit release gates.
Public directory submission/review is separate from GitHub distribution. The MIT
license in the repository root applies to this plugin.
