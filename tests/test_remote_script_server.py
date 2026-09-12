import ast
from pathlib import Path
from types import SimpleNamespace

SCRIPT_PATH = Path(__file__).parents[1] / "ableton_live_mcp/remote_script/__init__.py"


def _server_thread_method():
    tree = ast.parse(SCRIPT_PATH.read_text())
    cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AbletonMCP"
    )
    return next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "_server_thread"
    )


def test_idle_socket_timeout_is_not_logged_as_an_accept_error():
    class LegacySocketTimeout(OSError):
        """Model Live 11 where socket.timeout is not caught by TimeoutError."""

    class DifferentTimeoutError(OSError):
        pass

    class IdleServer:
        def __init__(self, owner):
            self.owner = owner
            self.accept_count = 0

        def settimeout(self, value):
            assert value == 1.0

        def accept(self):
            self.accept_count += 1
            if self.accept_count == 2:
                self.owner.running = False
            raise LegacySocketTimeout("timed out")

    namespace = {
        "socket": SimpleNamespace(timeout=LegacySocketTimeout),
        "TimeoutError": DifferentTimeoutError,
        "threading": __import__("threading"),
        "time": SimpleNamespace(sleep=lambda _seconds: None),
    }
    method = _server_thread_method()
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SCRIPT_PATH), "exec"), namespace)

    bridge = SimpleNamespace(
        running=True,
        client_sockets=[],
        client_threads=[],
        log_messages=[],
        show_message=lambda _message: None,
    )
    bridge.log_message = bridge.log_messages.append
    bridge.server = IdleServer(bridge)

    namespace["_server_thread"](bridge)

    assert bridge.log_messages == ["Server thread started", "Server thread stopped"]
