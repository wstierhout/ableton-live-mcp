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


class AutomationEnvelope:
    """Minimal public envelope API shared by automation command regressions."""

    def __init__(self, events):
        self.events = events
        self.steps = [(0.0, 4.0, 0.5)]
        self.reads = []
        self.parameter = None

    def insert_step(self, time, duration, value):
        self.events.append(("insert", time, duration, value))
        self.steps.append((time, duration, value))
        if self.parameter is not None:
            self.parameter.automation_state = 1

    def value_at_time(self, time):
        self.reads.append(time)
        for start, duration, value in reversed(self.steps):
            if start <= time <= start + duration:
                return value
        return 0.25


class AutomationParameter:
    name, min, max, is_enabled = "Gain", 0.0, 1.0, True
    is_quantized = False
    automation_state = 0

    def __init__(self, events):
        self.events = events
        self._value = 0.25

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self.events.append(("seed", value))
        self._value = value

    def str_for_value(self, value):
        return str(value)


@pytest.fixture
def automation(bridge):
    events = []
    param = AutomationParameter(events)
    envelope = AutomationEnvelope(events)
    envelope.parameter = param

    def clear(parameter):
        assert parameter is param
        events.append(("clear",))
        envelope.steps.clear()
        param.automation_state = 0

    def lookup(parameter):
        assert parameter is param
        events.append(("lookup",))
        return clip.envelope

    def create(parameter):
        assert parameter is param
        events.append(("create",))
        clip.envelope = envelope
        return envelope

    clip = SimpleNamespace(
        name="Fixture",
        length=4.0,
        end_marker=4.0,
        loop_end=4.0,
        envelope=envelope,
        clear_envelope=clear,
        automation_envelope=lookup,
        create_automation_envelope=create,
    )
    bridge._get_clip = lambda *args: clip
    bridge._get_device = lambda *args: SimpleNamespace(name="Utility", parameters=[param])
    bridge._song.view = SimpleNamespace(detail_clip=None)
    return SimpleNamespace(clip=clip, parameter=param, envelope=envelope, events=events)
