"""Load the real bridge without Live; only its host and scheduling are simulated."""

import ast
import importlib.util
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

import ableton_live_mcp
from ableton_live_mcp.connection import AbletonCommandError


@pytest.fixture(scope="session")
def bridge_module():
    source = Path(ableton_live_mcp.__file__).parent / "remote_script/__init__.py"
    ast.parse(source.read_text(), feature_version=(3, 7))
    spec = importlib.util.spec_from_file_location("test_live_bridge", source)
    module = importlib.util.module_from_spec(spec)
    host = ModuleType("_Framework.ControlSurface")
    host.ControlSurface = object
    with patch.dict(sys.modules, {"_Framework": ModuleType("_Framework"), host.__name__: host}):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def bridge(bridge_module):
    live = bridge_module.AbletonMCP.__new__(bridge_module.AbletonMCP)
    live._main_thread = threading.current_thread()
    live._receipts, live._receipt_lock = OrderedDict(), threading.Lock()
    live._ALL_COMMANDS = dict(live._ALL_COMMANDS)
    live.pending, live.edits = [], []
    live.schedule_message = lambda delay, callback: live.pending.append(callback)
    live.log_message = lambda message: None
    live._song = SimpleNamespace(
        tracks=[],
        is_playing=False,
        tempo=120,
        current_song_time=0,
        begin_undo_step=lambda: live.edits.append("begin"),
        end_undo_step=lambda: live.edits.append("end"),
    )

    def dispatch(command):
        replies = []
        worker = threading.Thread(target=lambda: replies.append(live._process_command(command)))
        worker.start()
        deadline = time.monotonic() + 2
        while worker.is_alive() and time.monotonic() < deadline:
            if live.pending:
                live.pending.pop(0)()
            else:
                time.sleep(0.001)
        worker.join(0.1)
        assert not worker.is_alive(), "Bridge dispatch did not finish"
        return replies[0]

    live.dispatch = dispatch
    return live


@pytest.fixture
def bridge_connection(bridge):
    def send(command, params=None):
        reply = bridge.dispatch({"type": command, "params": params or {}})
        if reply["status"] == "error":
            raise AbletonCommandError(reply["message"], reply.get("details"))
        return reply["result"]

    return SimpleNamespace(send_command=send)
