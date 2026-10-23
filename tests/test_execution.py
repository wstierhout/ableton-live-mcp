import asyncio
import threading

from mcp.types import ToolAnnotations

from ableton_live_mcp.execution import WorkerMCP, cancellable_wait, cleanup_scope


def test_long_mutation_does_not_block_read_and_mutations_do_not_interleave():
    async def exercise():
        server = WorkerMCP("test")
        started, release = threading.Event(), threading.Event()
        events = []

        @server.tool()
        def long_edit() -> str:
            events.append("first-start")
            started.set()
            assert release.wait(2)
            events.append("first-end")
            return '{"edited": true}'

        @server.tool()
        def other_edit() -> str:
            events.append("second")
            return "done"

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def status() -> str:
            return '{"alive": true}'

        first = asyncio.create_task(server.call_tool("long_edit", {}))
        while not started.is_set():
            await asyncio.sleep(0.001)
        second = asyncio.create_task(server.call_tool("other_edit", {}))
        result = await asyncio.wait_for(server.call_tool("status", {}), 0.5)
        assert result.structuredContent["data"] == {"alive": True}
        assert events == ["first-start"]
        release.set()
        result = await first
        await second
        assert result.structuredContent["data"] == {"edited": True}
        assert events == ["first-start", "first-end", "second"]

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_cancellation_runs_cleanup_before_next_edit():
    async def exercise():
        server = WorkerMCP("test")
        started, cleaned = threading.Event(), threading.Event()

        @server.tool()
        def capture() -> str:
            try:
                started.set()
                cancellable_wait(10)
            finally:
                with cleanup_scope():
                    cancellable_wait(0.02)
                    cleaned.set()
            return "done"

        @server.tool()
        def next_edit() -> str:
            assert cleaned.is_set()
            return "done"

        running = asyncio.create_task(server.call_tool("capture", {}))
        while not started.is_set():
            await asyncio.sleep(0.001)
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        result = await asyncio.wait_for(server.call_tool("next_edit", {}), 1)
        assert not result.isError
        await server.drain_workers()

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_errors_keep_structured_partial_results():
    from ableton_live_mcp.connection import AbletonCommandError

    async def exercise():
        server = WorkerMCP("test")

        @server.tool()
        def bad() -> str:
            raise AbletonCommandError("step failed", {"completed_count": 2, "operation_id": "abc"})

        result = await server.call_tool("bad", {})
        assert result.isError
        assert result.structuredContent["error"]["completed_count"] == 2
        assert result.structuredContent["error"]["operation_id"] == "abc"
        tool = (await server.list_tools())[0]
        assert "data" in tool.outputSchema["properties"]

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_cancelled_clip_edit_finishes_its_started_write(monkeypatch):
    import json

    from ableton_live_mcp.connection import AbletonConnection
    from ableton_live_mcp.tools import generators

    async def exercise():
        server = WorkerMCP("edit-boundary")
        resized, release, finished = threading.Event(), threading.Event(), threading.Event()
        sent = []

        class Socket:
            reply = b""

            def settimeout(self, value):
                pass

            def sendall(self, data):
                command = json.loads(data)["type"]
                sent.append(command)
                if command == "create_clip":
                    response = {"status": "error", "message": "already has a clip"}
                else:
                    if command == "set_clip_loop":
                        resized.set()
                        assert release.wait(2)
                    response = {"status": "success", "result": {}}
                self.reply = json.dumps(response).encode()

            def recv(self, size):
                result, self.reply = self.reply, b""
                return result

        conn = AbletonConnection("unused", 1, Socket())
        monkeypatch.setattr(generators, "get_ableton_connection", lambda: conn)

        @server.tool()
        def generate() -> str:
            generators._write_clip(
                0, 0, 8.0, [{"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100}]
            )
            finished.set()
            return "done"

        running = asyncio.create_task(server.call_tool("generate", {}))
        while not resized.is_set():
            await asyncio.sleep(0.001)
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        release.set()
        await asyncio.wait_for(server.drain_workers(), 2)
        assert finished.is_set()
        assert sent == ["create_clip", "set_clip_loop", "add_notes_to_clip"]

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_cancellation_before_first_command_sends_nothing():
    from ableton_live_mcp.cancellation import ToolCancelled, operation_scope
    from ableton_live_mcp.connection import AbletonConnection

    cancel = threading.Event()
    cancel.set()

    class Socket:
        def sendall(self, _):
            raise AssertionError("should not send")

    conn = AbletonConnection("unused", 1, Socket())
    import pytest

    with operation_scope(cancel, finish_started_edits=True), pytest.raises(ToolCancelled):
        conn.send_command("create_midi_track")


def test_shutdown_cancellation_waits_for_recovery_and_rejects_new_work():
    async def exercise():
        server = WorkerMCP("shutdown")
        started, recovering, release, cleaned = (threading.Event() for _ in range(4))

        @server.tool()
        def capture() -> str:
            try:
                started.set()
                cancellable_wait(10)
            finally:
                with cleanup_scope():
                    recovering.set()
                    assert release.wait(2)
                    cleaned.set()
            return "done"

        running = asyncio.create_task(server.call_tool("capture", {}))
        while not started.is_set():
            await asyncio.sleep(0.001)
        drain = asyncio.create_task(server.drain_workers())
        while not recovering.is_set():
            await asyncio.sleep(0.001)
        drain.cancel()
        await asyncio.sleep(0.01)
        assert not drain.done()
        denied = await server.call_tool("capture", {})
        assert denied.isError and denied.structuredContent["error"]["type"] == "ServerClosing"
        release.set()
        try:
            await asyncio.wait_for(drain, 1)
        except asyncio.CancelledError:
            pass
        await running
        assert cleaned.is_set()

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_shutdown_is_shielded_from_anyio_cancel_scope():
    import anyio

    from ableton_live_mcp.execution import finish_cleanup

    async def exercise():
        events = []

        async def cleanup():
            await anyio.sleep(0.01)
            events.append("done")

        with anyio.CancelScope() as scope:
            scope.cancel()
            await finish_cleanup(cleanup())
        assert events == ["done"]

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_json_response_does_not_duplicate_text_in_structured_message():
    async def exercise():
        server = WorkerMCP("payload")

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def read() -> str:
            return '{"large": [1, 2, 3]}'

        reply = await server.call_tool("read", {})
        assert reply.structuredContent == {"data": {"large": [1, 2, 3]}, "error": None}
        assert reply.content[0].text == '{"large": [1, 2, 3]}'

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_cancelled_lifespan_joins_warmup_before_disconnecting(monkeypatch):
    from ableton_live_mcp import app

    async def exercise():
        started, release, stopping = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []

        async def warmup():
            started.set()
            await release.wait()
            events.append("connected")

        monkeypatch.setattr(app, "_connect_on_startup", warmup)
        monkeypatch.setattr(app, "disconnect_ableton", lambda: events.append("disconnected"))
        server = WorkerMCP("lifespan")

        async def lifecycle():
            async with app.server_lifespan(server):
                await started.wait()
                stopping.set()

        task = asyncio.create_task(lifecycle())
        await stopping.wait()
        while not getattr(server, "_closing", False):
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and events == []
        release.set()
        try:
            await asyncio.wait_for(task, 1)
        except asyncio.CancelledError:
            pass
        assert events == ["connected", "disconnected"]

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))
