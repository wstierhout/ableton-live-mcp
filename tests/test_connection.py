"""Protocol-level tests against a fake Remote Script socket server."""

import json
import socket
import threading

import pytest

from ableton_live_mcp.connection import AbletonConnection


class FakeAbleton:
    """Minimal stand-in for the Remote Script's socket server."""

    def __init__(self, responder):
        self.responder = responder
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.server.accept()
        buffer = b""
        with conn:
            while True:
                data = conn.recv(8192)
                if not data:
                    break
                buffer += data
                try:
                    request = json.loads(buffer.decode())
                except json.JSONDecodeError:
                    continue  # partial frame - keep reading
                buffer = b""
                reply = self.responder(request)
                conn.sendall(json.dumps(reply).encode())

    def close(self):
        self.server.close()


def test_send_command_round_trip():
    fake = FakeAbleton(
        lambda req: {"status": "success", "result": {"echo": req["type"], "params": req["params"]}}
    )
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    try:
        result = conn.send_command("get_session_info", {"x": 1})
        assert result == {"echo": "get_session_info", "params": {"x": 1}}
    finally:
        conn.disconnect()
        fake.close()


def test_error_status_raises():
    fake = FakeAbleton(lambda req: {"status": "error", "message": "boom"})
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    try:
        with pytest.raises(Exception, match="boom"):
            conn.send_command("get_session_info")
    finally:
        conn.disconnect()
        fake.close()


def test_chunked_response_reassembly():
    big = {"status": "success", "result": {"blob": "x" * 100_000}}

    fake = FakeAbleton(lambda req: big)
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    try:
        result = conn.send_command("get_session_info")
        assert len(result["blob"]) == 100_000
    finally:
        conn.disconnect()
        fake.close()


def test_long_running_command_gets_full_timeout(monkeypatch):
    """command_timeout must reach the socket (regression: receive_full_response
    used to clobber it with a hardcoded 15.0)."""
    import ableton_live_mcp.connection as C

    seen = {}
    fake = FakeAbleton(lambda req: {"status": "success", "result": {}})
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    orig = conn.receive_full_response

    def spy(sock, timeout=15.0, buffer_size=8192):
        seen["t"] = timeout
        return orig(sock, timeout=timeout, buffer_size=buffer_size)

    try:
        conn.receive_full_response = spy
        conn.send_command("create_audio_clip", {})
        assert seen["t"] == C.command_timeout("create_audio_clip") == 65.0
    finally:
        conn.disconnect()
        fake.close()


def test_concurrent_calls_do_not_interleave():
    """The io lock must serialize concurrent tool calls on the shared socket."""
    import threading as th

    def slow_responder(req):
        import time as t

        t.sleep(0.05)
        return {"status": "success", "result": {"echo": req["params"]["n"]}}

    fake = FakeAbleton(slow_responder)
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    results, errors = [], []

    def call(n):
        try:
            results.append((n, conn.send_command("get_session_info", {"n": n})["echo"]))
        except Exception as e:
            errors.append(str(e))

    threads = [th.Thread(target=call, args=(i,)) for i in range(4)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert all(sent == echoed for sent, echoed in results)
    finally:
        conn.disconnect()
        fake.close()
