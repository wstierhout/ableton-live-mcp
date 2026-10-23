"""Static checks on the Remote Script dispatch tables and the server/script contract."""

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPT_SRC = (ROOT / "ableton_live_mcp" / "remote_script" / "__init__.py").read_text()
SCRIPT_AST = ast.parse(SCRIPT_SRC)


def _ableton_class():
    for node in ast.walk(SCRIPT_AST):
        if isinstance(node, ast.ClassDef) and node.name == "AbletonMCP":
            return node
    raise AssertionError("AbletonMCP class not found")


def _dispatch_table(table_name):
    """Extract {command: handler_method} from a class-level dict-of-lambdas."""
    cls = _ableton_class()
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == table_name for t in node.targets
        ):
            table = {}
            for key, value in zip(node.value.keys, node.value.values):
                methods = {
                    n.func.attr
                    for n in ast.walk(value)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and getattr(n.func.value, "id", None) == "s"
                }
                table[key.value] = methods
            return table
    raise AssertionError(f"{table_name} table not found")


MUTATING = _dispatch_table("_MUTATING_COMMANDS")
READONLY = _dispatch_table("_READONLY_COMMANDS")
CLASS_METHODS = {n.name for n in _ableton_class().body if isinstance(n, ast.FunctionDef)}


def test_all_dispatched_methods_exist():
    for table_name, table in (("_MUTATING_COMMANDS", MUTATING), ("_READONLY_COMMANDS", READONLY)):
        for command, methods in table.items():
            missing = methods - CLASS_METHODS
            assert not missing, f"{table_name}[{command!r}] references missing methods: {missing}"


def test_no_command_registered_twice():
    overlap = MUTATING.keys() & READONLY.keys()
    assert not overlap, f"commands in both tables: {overlap}"


def test_every_self_method_call_resolves():
    """Every self._x(...) call inside the class must hit a defined method.

    Regression guard: a half-applied refactor once left _load_browser_item
    calling a helper that didn't exist.
    """
    FRAMEWORK_ATTRS = {  # provided by ControlSurface / Live at runtime
        "log_message",
        "application",
        "schedule_message",
        "song",
        "show_message",
        "request_rebuild_midi_map",
        "disconnect",
    }
    cls = _ableton_class()
    called = {
        node.func.attr
        for node in ast.walk(cls)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and getattr(node.func.value, "id", None) == "self"
    }
    missing = called - CLASS_METHODS - FRAMEWORK_ATTRS
    assert not missing, f"self.<method> calls with no definition: {missing}"


def test_client_timeout_exceeds_actual_bridge_wait(bridge, bridge_module, monkeypatch):
    from types import SimpleNamespace

    from ableton_live_mcp.connection import SOCKET_HEADROOM, command_timeout

    waits = []
    monkeypatch.setattr(
        bridge_module.queue,
        "Queue",
        lambda: SimpleNamespace(
            get=lambda timeout: waits.append(timeout) or {}, put=lambda response: None
        ),
    )
    commands = [("get_session_info", {})] + [(name, {}) for name in bridge._COMMAND_TIMEOUTS]
    commands.append(("batch", {"commands": [{"type": "create_audio_clip"}]}))
    for command, params in commands:
        bridge.dispatch({"type": command, "params": params})
        assert command_timeout(command, params) == waits[-1] + SOCKET_HEADROOM


def test_quantize_grid_vocabulary_stays_in_sync():
    """clips.VALID_GRIDS pre-validates what the Remote Script's _Q_BASE accepts;
    adding a grid to one side without the other is silently dead."""
    from ableton_live_mcp.tools.clips import VALID_GRIDS

    cls = _ableton_class()
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_Q_BASE" for t in node.targets
        ):
            script_grids = set(ast.literal_eval(node.value))
            assert script_grids == set(VALID_GRIDS)
            return
    raise AssertionError("_Q_BASE not found in the Remote Script")


def test_cancellation_read_command_classification_matches_bridge():
    from ableton_live_mcp.cancellation import READ_COMMANDS

    assert READ_COMMANDS == READONLY.keys()
