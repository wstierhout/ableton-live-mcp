"""Real stdio MCP handshake/tool-call test, without contacting a running Live set."""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_handshake_and_readonly_capability_call():
    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ableton_live_mcp.server"],
            env={
                **os.environ,
                "ABLETON_TOOLSETS": "analysis",
                "ABLETON_HOST": "127.0.0.1",
                "ABLETON_PORT": "1",
            },
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                info = await session.initialize()
                assert info.serverInfo.name == "Ableton Live MCP Server"
                tools = await session.list_tools()
                assert "analyze_audio_file" in {tool.name for tool in tools.tools}
                result = await session.call_tool("describe_capabilities", {})
                assert not result.isError
                assert "groups" in result.content[0].text
                assert "groups" in result.structuredContent["data"]
                failed = await session.call_tool(
                    "analyze_audio_file", {"path": "/nonexistent/audio.wav"}
                )
                assert failed.isError
                assert failed.structuredContent["error"]["type"] == "ValueError"

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
