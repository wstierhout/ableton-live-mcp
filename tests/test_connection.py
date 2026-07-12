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

    def spy(buffer_size=8192):
        seen["t"] = conn.sock.gettimeout()
        return orig(buffer_size=buffer_size)

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


def test_timeout_reports_modal_dialog_hint(monkeypatch):
    """A silent Live must surface the modal-dialog guidance (regression: the
    receive loop used to swallow TimeoutError into a generic message) and the
    dead socket must be closed, not leaked."""
    import ableton_live_mcp.connection as C

    def never_respond(req):
        import time as t

        t.sleep(10)
        return {"status": "success", "result": {}}

    monkeypatch.setattr(C, "REMOTE_DEFAULT_TIMEOUT", 0.1)
    monkeypatch.setattr(C, "SOCKET_HEADROOM", 0.1)
    fake = FakeAbleton(never_respond)
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    try:
        with pytest.raises(C.AbletonTimeoutError, match="modal dialog"):
            conn.send_command("get_session_info")
        assert conn.sock is None  # closed, ready for a clean reconnect
    finally:
        conn.disconnect()
        fake.close()


def test_app_level_error_keeps_connection_open():
    """A status=error reply is an application error, not a transport failure:
    the same socket must serve the next command without reconnecting."""
    import ableton_live_mcp.connection as C

    calls = {"n": 0}

    def responder(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "error", "message": "no such track"}
        return {"status": "success", "result": {"ok": True}}

    fake = FakeAbleton(responder)
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    try:
        with pytest.raises(C.AbletonCommandError, match="no such track"):
            conn.send_command("get_track_info", {"track_index": 99})
        sock_before = conn.sock
        assert sock_before is not None
        assert conn.send_command("get_session_info") == {"ok": True}
        assert conn.sock is sock_before  # same socket, no reconnect
    finally:
        conn.disconnect()
        fake.close()


def test_connection_closed_mid_response():
    """The peer dying mid-frame must raise the connection-lost error and close
    the local socket."""
    import ableton_live_mcp.connection as C

    class DyingAbleton(FakeAbleton):
        def _serve(self):
            conn, _ = self.server.accept()
            conn.recv(8192)
            conn.sendall(b'{"status": "succ')  # partial frame
            conn.close()

    fake = DyingAbleton(lambda req: None)
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    try:
        with pytest.raises(C.AbletonConnectionLostError, match="Connection to Ableton lost"):
            conn.send_command("get_session_info")
        assert conn.sock is None
    finally:
        conn.disconnect()
        fake.close()


def test_multibyte_utf8_split_across_chunks():
    """A multibyte character straddling two TCP sends must not abort the
    receive loop (regression: UnicodeDecodeError used to kill the command)."""

    class SplitAbleton(FakeAbleton):
        def _serve(self):
            import time as t

            conn, _ = self.server.accept()
            conn.recv(8192)
            payload = json.dumps(
                {"status": "success", "result": {"name": "Träck \U0001f3b9"}}
            ).encode("utf-8")
            # Split inside the last multibyte character.
            cut = len(payload) - 3
            conn.sendall(payload[:cut])
            t.sleep(0.05)
            conn.sendall(payload[cut:])

    fake = SplitAbleton(lambda req: None)
    conn = AbletonConnection(host="127.0.0.1", port=fake.port)
    try:
        result = conn.send_command("get_track_info")
        assert result["name"] == "Träck \U0001f3b9"
    finally:
        conn.disconnect()
        fake.close()
