"""Bounded worker execution and cooperative cancellation for synchronous tools."""

import asyncio
import inspect
import json
import threading
from functools import wraps
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from .cancellation import (
    ToolCancelled as ToolCancelled,
)
from .cancellation import (
    _local as _local,
)
from .cancellation import (
    cancellable_wait as cancellable_wait,
)
from .cancellation import (
    check_cancelled,
    operation_scope,
)
from .cancellation import (
    cleanup_scope as cleanup_scope,
)

LIVE_EDIT_LOCK = threading.RLock()


class ToolOutput(BaseModel):
    """Text compatibility plus actual JSON fields, including structured failures."""

    data: dict[str, Any]
    error: dict[str, Any] | None = None


async def finish_cleanup(awaitable):
    """Complete cleanup under both AnyIO scope and native asyncio cancellation."""
    task = asyncio.ensure_future(awaitable)
    cancelled = None
    with anyio.CancelScope(shield=True):
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError as exc:
                if task.cancelled():
                    raise
                cancelled = exc
    if cancelled is not None:
        raise cancelled
    return result


class WorkerMCP(FastMCP):
    def add_tool(self, fn, *args, **kwargs):
        enabled = getattr(self, "enabled_tool_modules", None)
        if enabled is not None and fn.__module__ not in enabled:
            return
        if inspect.iscoroutinefunction(fn):
            return super().add_tool(fn, *args, **kwargs)
        annotations = kwargs.get("annotations")
        mutates = not (annotations and annotations.readOnlyHint)

        @wraps(fn)
        async def run(**arguments):
            if getattr(self, "_closing", False):
                message = "Server is shutting down; no new work was started"
                return CallToolResult(
                    isError=True,
                    content=[TextContent(type="text", text=message)],
                    structuredContent={
                        "data": {},
                        "error": {"type": "ServerClosing", "message": message},
                    },
                )
            # Construct in an async context (AnyIO selects the running backend).
            if not hasattr(self, "_worker_limit"):
                self._worker_limit = anyio.CapacityLimiter(8)
                self._edit_limit = anyio.CapacityLimiter(1)
                self._heavy_limit = anyio.CapacityLimiter(2)
            cancel = threading.Event()

            def work():
                with operation_scope(
                    cancel, finish_started_edits=mutates and fn.__name__ != "record_section"
                ):
                    return perform()

            def perform():
                acquired = False
                try:
                    if mutates:
                        while not acquired:
                            check_cancelled()
                            acquired = LIVE_EDIT_LOCK.acquire(timeout=0.1)
                    check_cancelled()
                    result = fn(**arguments)
                    message = (
                        result if isinstance(result, str) else json.dumps(result, allow_nan=False)
                    )
                    try:
                        data = json.loads(message) if isinstance(result, str) else result
                    except (ValueError, TypeError):
                        data = {"message": message}
                    if not isinstance(data, dict):
                        data = {"items": data}
                    return CallToolResult(
                        content=[TextContent(type="text", text=message)],
                        structuredContent={"data": data, "error": None},
                    )
                except Exception as exc:
                    error = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        **getattr(exc, "details", {}),
                    }
                    return CallToolResult(
                        isError=True,
                        content=[TextContent(type="text", text=str(exc))],
                        structuredContent={"data": {}, "error": error},
                    )
                finally:
                    if acquired:
                        LIVE_EDIT_LOCK.release()

            async def execute():
                # Wait outside the worker pool. Queued edits must not consume
                # every worker while a real-time recording owns the edit lock.
                limiter = (
                    self._edit_limit
                    if mutates
                    else (
                        self._heavy_limit
                        if fn.__name__.startswith(("als_", "adg_"))
                        or fn.__name__ == "analyze_audio_file"
                        else None
                    )
                )
                if limiter is not None:
                    async with limiter:
                        return await anyio.to_thread.run_sync(work, limiter=self._worker_limit)
                return await anyio.to_thread.run_sync(work, limiter=self._worker_limit)

            try:
                # Shield the child, not the request: cancellation signals work,
                # while its capacity slot remains held through recovery. Repeated
                # cancelled calls cannot create an unbounded number of workers.
                task = asyncio.create_task(execute())
                if not hasattr(self, "_active_runs"):
                    self._active_runs = {}
                self._active_runs[task] = cancel
                task.add_done_callback(lambda done: self._active_runs.pop(done, None))
                return await asyncio.shield(task)
            except BaseException:
                cancel.set()
                raise

        run.__signature__ = inspect.signature(fn, eval_str=True).replace(
            return_annotation=ToolOutput
        )
        run.__annotations__ = {**fn.__annotations__, "return": ToolOutput}
        return super().add_tool(run, *args, **kwargs)

    async def drain_workers(self):
        self._closing = True

        async def drain():
            active = getattr(self, "_active_runs", {})
            for cancel in list(active.values()):
                cancel.set()
            if active:
                await asyncio.gather(*list(active), return_exceptions=True)

        await finish_cleanup(drain())
