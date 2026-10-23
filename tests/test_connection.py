"""Protocol behavior over loopback sockets and deterministic fragmented replies."""

import json
import socket
import threading
from types import SimpleNamespace

import pytest

from ableton_live_mcp import connection as C


class FakeAbleton:
    def __init__(self, responder):
        self.responder, self.error = responder, None
        self.stopped = threading.Event()
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.server.settimeout(2)
        self.connection = C.AbletonConnection("127.0.0.1", self.server.getsockname()[1])
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        try:
            peer, _ = self.server.accept()
            buffer = b""
            with peer:
                while data := peer.recv(8192):
                    buffer += data
                    try:
                        request = json.loads(buffer)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    buffer = b""
                    reply = self.responder(request)
                    if reply is None:
                        self.stopped.wait(2)
                        return
                    if isinstance(reply, bytes):
                        peer.sendall(reply)
                        return  # deliberately truncated response
                    peer.sendall(json.dumps(reply, ensure_ascii=False).encode())
        except Exception as exc:
            if not self.stopped.is_set():
                self.error = exc

    def __enter__(self):
        self.thread.start()
        return self.connection

    def __exit__(self, *_):
        self.stopped.set()
        self.connection.disconnect()
        self.server.close()
        self.thread.join(2)
        assert not self.thread.is_alive(), "Fixture socket thread leaked"
        assert self.error is None, self.error


def test_send_command_round_trip():
    with FakeAbleton(lambda req: {"status": "success", "result": req["params"]}) as conn:
        assert conn.send_command("get_track_info", {"track_index": 2}) == {"track_index": 2}


def test_chunked_response_reassembly():
    with FakeAbleton(lambda req: {"status": "success", "result": {"blob": "x" * 100_000}}) as conn:
        assert conn.send_command("get_session_info")["blob"] == "x" * 100_000


def test_long_running_command_gets_full_timeout():
    with FakeAbleton(lambda req: {"status": "success", "result": {}}) as conn:
        receive = conn.receive_full_response

        def inspect_timeout():
            assert conn.sock.gettimeout() == C.command_timeout("create_audio_clip") == 65.0
            return receive()

        conn.receive_full_response = inspect_timeout
        conn.send_command("create_audio_clip")


def test_concurrent_calls_do_not_interleave():
    with FakeAbleton(lambda req: {"status": "success", "result": req["params"]}) as conn:
        results, errors = [], []
        ready = threading.Barrier(4, timeout=2)

        def call(number):
            try:
                ready.wait()
                results.append((number, conn.send_command("get_track_info", {"n": number})["n"]))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=call, args=(number,)) for number in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        assert not any(thread.is_alive() for thread in threads)
        assert not errors
        assert sorted(results) == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_timeout_reports_modal_dialog_hint(monkeypatch):
    monkeypatch.setattr(C, "REMOTE_DEFAULT_TIMEOUT", 0.1)
    monkeypatch.setattr(C, "SOCKET_HEADROOM", 0.1)
    with FakeAbleton(lambda req: None) as conn:
        with pytest.raises(C.AbletonTimeoutError, match="modal dialog"):
            conn.send_command("get_session_info")
        assert conn.sock is None


def test_app_level_error_keeps_connection_open():
    replies = iter(
        [
            {"status": "error", "message": "no such track"},
            {"status": "success", "result": {"ok": True}},
        ]
    )
    with FakeAbleton(lambda req: next(replies)) as conn:
        with pytest.raises(C.AbletonCommandError, match="no such track"):
            conn.send_command("get_track_info", {"track_index": 99})
        sock = conn.sock
        assert conn.send_command("get_session_info") == {"ok": True}
        assert conn.sock is sock


def test_connection_closed_mid_response():
    with FakeAbleton(lambda req: b'{"status": "succ') as conn:
        with pytest.raises(C.AbletonConnectionLostError, match="Connection to Ableton lost"):
            conn.send_command("get_session_info")
        assert conn.sock is None


def test_multibyte_utf8_split_across_chunks():
    payload = '{"status":"success","result":{"name":"Träck 🎹"}}'.encode()
    cut = payload.index("🎹".encode()) + 2
    chunks = iter((payload[:cut], payload[cut:]))
    # Control recv boundaries; TCP may coalesce separate sends.
    sock = SimpleNamespace(
        settimeout=lambda _: None,
        sendall=lambda _: None,
        recv=lambda _: next(chunks, b""),
        close=lambda: None,
    )
    result = C.AbletonConnection("unused", 1, sock).send_command("get_track_info")
    assert result == {"name": "Träck 🎹"}


def test_trickled_response_has_one_overall_deadline(monkeypatch):
    clock, closed = [0.0], []
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(C, "REMOTE_DEFAULT_TIMEOUT", 0.2)
    monkeypatch.setattr(C, "SOCKET_HEADROOM", 0.0)

    def advance(seconds, reply=None):
        clock[0] += seconds
        return reply

    sock = SimpleNamespace(
        settimeout=lambda _: None,
        sendall=lambda _: advance(0.05),
        recv=lambda _: advance(0.08, b" "),
        close=lambda: closed.append(True),
    )
    with pytest.raises(C.AbletonTimeoutError) as error:
        C.AbletonConnection("unused", 1, sock).send_command("create_midi_track")
    assert closed and 0.2 <= clock[0] < 0.3
    assert error.value.details["may_have_applied"] is True
    assert error.value.details["operation_id"]


def test_structured_bridge_error_survives_connection():
    def reply(request):
        return {
            "status": "error",
            "message": "batch failed",
            "operation_id": request["operation_id"],
            "details": {"completed_count": 1, "failed_step": 2},
        }

    with FakeAbleton(reply) as conn:
        with pytest.raises(C.AbletonCommandError) as error:
            conn.send_command("batch", {"commands": []}, operation_id="known-id")
        assert error.value.details == {
            "completed_count": 1,
            "failed_step": 2,
            "operation_id": "known-id",
        }


@pytest.mark.parametrize(
    "response",
    [
        [],
        {"status": "unexpected", "result": {}},
        {"result": {}},
        {"status": "success"},
        {"status": "success", "result": None},
        {"status": "error", "message": None},
        {"status": "error", "message": "failed", "details": []},
        {"status": "success", "result": {}, "operation_id": "another-edit"},
    ],
)
def test_malformed_response_retains_uncertain_edit_and_never_retries(response):
    requests = []

    def reply(request):
        requests.append(request)
        return response

    with FakeAbleton(reply) as conn:
        with pytest.raises(C.AbletonProtocolError) as error:
            conn.send_command("create_midi_track", operation_id="original-edit")
        assert error.value.details == {
            "operation_id": "original-edit",
            "state": "unknown",
            "may_have_applied": True,
        }
        assert len(requests) == 1
        assert conn.sock is None


def test_disconnect_waits_for_inflight_capability_probe():
    answering, release, disconnecting, disconnected = (threading.Event() for _ in range(4))
    results = []

    def reply(request):
        answering.set()
        assert release.wait(2)
        return {"status": "success", "result": {"capabilities": ["batch_preflight"]}}

    with FakeAbleton(reply) as conn:

        def disconnect():
            disconnecting.set()
            conn.disconnect()
            disconnected.set()

        probe = threading.Thread(
            target=lambda: results.append(conn.supports_capability("batch_preflight"))
        )
        closer = threading.Thread(target=disconnect)
        try:
            probe.start()
            assert answering.wait(2)
            closer.start()
            assert disconnecting.wait(2)
            assert not disconnected.wait(0.02)
        finally:
            release.set()
            probe.join(2)
            if closer.ident is not None:
                closer.join(2)
        assert results == [True] and disconnected.is_set()
        assert conn.sock is None


def test_capabilities_do_not_share_mutable_session_response():
    with FakeAbleton(
        lambda req: {"status": "success", "result": {"capabilities": ["batch_preflight"]}}
    ) as conn:
        result = conn.send_command("get_session_info")
        result["capabilities"].clear()
        assert conn.supports_capability("batch_preflight")
