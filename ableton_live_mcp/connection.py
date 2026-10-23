"""Socket connection to the AbletonMCP Remote Script (port 9877)."""

import json
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .cancellation import ToolCancelled, check_command_cancelled, command_will_send

ABLETON_HOST = os.environ.get("ABLETON_HOST", "localhost")
try:
    ABLETON_PORT = int(os.environ.get("ABLETON_PORT", "9877"))
except ValueError:
    raise RuntimeError(
        f"ABLETON_PORT must be an integer, got {os.environ.get('ABLETON_PORT')!r}"
    ) from None

logger = logging.getLogger("AbletonMCPServer")

# Client-side socket timeouts. The Remote Script enforces its own main-thread
# queue budget (10 s default; see _COMMAND_TIMEOUTS in the Remote Script for
# long-running overrides) - the client budget must exceed it or the client can
# drop the socket at the same instant the script replies. Keep these two maps
# in sync; SOCKET_HEADROOM covers transport latency on top of the script budget.
REMOTE_DEFAULT_TIMEOUT = 10.0
REMOTE_COMMAND_TIMEOUTS = {"create_audio_clip": 60.0}
SOCKET_HEADROOM = 5.0
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_REQUEST_BYTES = 10 * 1024 * 1024

_NOT_CONNECTED_MSG = (
    "Not connected to Ableton. Start Live, select AbletonMCP as the "
    "Control Surface (Settings > Tempo & MIDI > MIDI; older versions: "
    "Link, Tempo & MIDI or Link/MIDI > MIDI), set Input/Output to None, and run "
    "`ableton-live-mcp doctor`."
)


class AbletonError(Exception):
    """Base class for everything this module raises."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


class AbletonNotConnectedError(AbletonError):
    """No connection to Live could be established."""


class AbletonTimeoutError(AbletonError):
    """Live did not answer within the command's budget."""


class AbletonConnectionLostError(AbletonError):
    """The socket died mid-conversation."""


class AbletonProtocolError(AbletonError):
    """The bridge replied, but its response cannot confirm the command's outcome."""


class AbletonCommandError(AbletonError):
    """Live received the command and reported an application-level error.

    The connection itself is healthy; it stays open.
    """


def command_timeout(command_type: str, params: dict | None = None) -> float:
    """Client socket budget for one command: remote budget + transport headroom.
    A batch inherits the longest budget of its sub-commands, mirroring the
    Remote Script's timeout logic."""
    budget = REMOTE_COMMAND_TIMEOUTS.get(command_type, REMOTE_DEFAULT_TIMEOUT)
    if command_type == "batch" and params:
        for cmd in params.get("commands") or []:
            if isinstance(cmd, dict):
                budget = max(budget, REMOTE_COMMAND_TIMEOUTS.get(cmd.get("type", ""), 0.0))
    return budget + SOCKET_HEADROOM


@dataclass
class AbletonConnection:
    host: str
    port: int
    sock: socket.socket | None = None

    def __post_init__(self) -> None:
        # One request/response in flight at a time: concurrent MCP tool calls
        # must not interleave frames on the shared socket.
        self._io_lock = threading.RLock()
        self._deadline = None
        self._bridge_info = None

    def connect(self) -> bool:
        """Connect to the Ableton Remote Script socket server"""
        with self._io_lock:
            return self._connect()

    def _connect(self) -> bool:
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(None)
            logger.info(f"Connected to Ableton at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Ableton at {self.host}:{self.port}: {str(e)}")
            self.disconnect()
            return False

    def disconnect(self) -> None:
        """Disconnect from the Ableton Remote Script"""
        with self._io_lock:
            self._disconnect()

    def _disconnect(self) -> None:
        self._bridge_info = None
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Ableton: {str(e)}")
            finally:
                self.sock = None

    def diagnostics(self):
        from . import __version__

        info = self.send_command("get_session_info")
        return {
            "server_version": __version__,
            "bridge_version": info.get("bridge_version"),
            "live_version": info.get("live_version"),
            "bridge_protocol": info.get("bridge_protocol", 1),
            "capabilities": info.get("capabilities", []),
            "update_required": info.get("bridge_protocol", 1) < 2,
            "remedy": "Run ableton-live-mcp install and reload the Control Surface.",
        }

    def _capabilities(self):
        with self._io_lock:
            if self._bridge_info is None:
                self.send_command("get_session_info")
            return dict(self._bridge_info or {})

    def supports_capability(self, capability):
        return capability in self._capabilities().get("capabilities", ())

    def require_capability(self, capability):
        info = self._capabilities()
        if capability not in info.get("capabilities", ()):
            raise AbletonCommandError(
                f"The installed bridge does not support {capability}. "
                "Run `ableton-live-mcp install` "
                "and reload AbletonMCP in Live. Existing read-only tools remain available.",
                {
                    "required_capability": capability,
                    "bridge_version": info.get("bridge_version"),
                },
            )

    def receive_full_response(self, buffer_size=8192) -> dict[str, Any]:
        """Receive one complete JSON reply, potentially in multiple chunks,
        and return it parsed. The caller sets the socket timeout.

        Raises TimeoutError if Live stops sending before the frame completes and
        ConnectionError if the peer closes mid-frame, so send_command can map
        each to its own user-facing message.
        """
        chunks: list[bytes] = []
        size = 0
        while True:
            if self._deadline is not None:
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Overall response deadline exceeded")
                self.sock.settimeout(remaining)
            chunk = self.sock.recv(buffer_size)
            if not chunk:
                if not chunks:
                    raise ConnectionError("Connection closed before receiving any data")
                raise ConnectionError("Connection closed mid-response (incomplete JSON)")
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ConnectionError("Response exceeds the 32 MiB limit")
            # Valid replies are objects; parse arrays too so send_command can
            # reject a complete wrong-shaped envelope without waiting for timeout.
            if not chunk.rstrip().endswith((b"}", b"]")):
                continue
            data = b"".join(chunks)
            try:
                response = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Incomplete frame (a multibyte char can straddle chunks) -
                # keep receiving until the parse succeeds or the timeout hits.
                continue
            logger.debug(f"Received complete response ({len(data)} bytes)")
            return response

    def send_command(
        self,
        command_type: str,
        params: dict[str, Any] | None = None,
        *,
        operation_id: str | None = None,
    ) -> Any:
        """Send a command to Ableton and return the response's result payload."""
        check_command_cancelled()
        operation_id = operation_id or uuid.uuid4().hex
        command = {"type": command_type, "params": params or {}, "operation_id": operation_id}
        payload = json.dumps(command, allow_nan=False).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise AbletonCommandError(
                "Request exceeds the bridge's 10 MiB limit; split the work before editing"
            )
        timeout = command_timeout(command_type, params)

        with self._io_lock:
            check_command_cancelled()
            if not self.sock and not self.connect():
                raise AbletonNotConnectedError(_NOT_CONNECTED_MSG)
            try:
                logger.info(f"Sending command: {command_type}")
                logger.debug(f"Command params: {params}")
                # One deadline for the whole round trip, sendall included.
                self.sock.settimeout(timeout)
                self._deadline = time.monotonic() + timeout
                command_will_send(command_type)
                self.sock.sendall(payload)
                response = self.receive_full_response()
                valid = (
                    isinstance(response, dict)
                    and (
                        response.get("status") == "success"
                        and isinstance(response.get("result"), dict)
                        or response.get("status") == "error"
                        and isinstance(response.get("message"), str)
                        and isinstance(response.get("details", {}), dict)
                    )
                    and response.get("operation_id", operation_id) == operation_id
                )
                if not valid:
                    raise AbletonProtocolError(
                        "Invalid response from the Ableton bridge; the outcome is uncertain. "
                        "Inspect Live state and the operation receipt before retrying.",
                        {
                            "operation_id": operation_id,
                            "state": "unknown",
                            "may_have_applied": True,
                        },
                    )
                if command_type == "get_session_info" and response["status"] == "success":
                    self._bridge_info = dict(response["result"])
                    self._bridge_info["capabilities"] = tuple(
                        self._bridge_info.get("capabilities", ())
                    )
            except ToolCancelled:
                raise
            except AbletonProtocolError:
                self.disconnect()
                raise
            except TimeoutError:
                logger.error("Socket timeout while waiting for response from Ableton")
                self.disconnect()
                raise AbletonTimeoutError(
                    "Timeout waiting for Ableton response. If ALL commands time out, a "
                    "modal dialog is likely open in Live (it freezes the Remote Script) - "
                    "ask the user to dismiss it (press Enter in Live). The outcome is uncertain: "
                    "inspect Live state and the operation receipt before retrying.",
                    {"operation_id": operation_id, "state": "unknown", "may_have_applied": True},
                ) from None
            except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                logger.error(f"Socket connection error: {str(e)}")
                self.disconnect()
                raise AbletonConnectionLostError(
                    f"Connection to Ableton lost ({e}). Is Live still running? "
                    "Run `ableton-live-mcp doctor` to diagnose. "
                    "Inspect state before repeating an edit.",
                    {"operation_id": operation_id, "state": "unknown", "may_have_applied": True},
                ) from None
            except Exception as e:
                logger.error(f"Error communicating with Ableton: {str(e)}")
                self.disconnect()
                raise AbletonError(
                    f"Communication error with Ableton: {str(e)}. Inspect state before retrying.",
                    {"operation_id": operation_id, "state": "unknown", "may_have_applied": True},
                ) from e
            finally:
                self._deadline = None

        logger.debug(f"Response status: {response['status']}")
        if response["status"] == "error":
            # Live executed the request and answered; the connection is fine.
            logger.error(f"Ableton error: {response.get('message')}")
            raise AbletonCommandError(
                response["message"],
                {
                    **response.get("details", {}),
                    "operation_id": operation_id,
                },
            )
        return response["result"]


# Global connection singleton (shared across all tool modules)
_ableton_connection: AbletonConnection | None = None
_connection_lock = threading.Lock()


def _existing_connection_alive() -> bool:
    """Best-effort liveness peek at the shared socket, without disturbing an
    in-flight command: if the io lock is held, someone is mid-conversation and
    the connection is evidently alive."""
    conn = _ableton_connection
    if conn is None or conn.sock is None:
        return False
    if not conn._io_lock.acquire(blocking=False):
        return True
    try:
        # MSG_PEEK on a zero-timeout socket: BlockingIOError means alive with
        # nothing pending; b"" means the remote end closed.
        conn.sock.settimeout(0)
        try:
            if conn.sock.recv(1, socket.MSG_PEEK) == b"":
                logger.warning("Existing connection was closed by the remote end")
                return False
        except (BlockingIOError, InterruptedError):
            pass
        finally:
            conn.sock.settimeout(None)
        return True
    except Exception as e:
        logger.warning(f"Existing connection is no longer valid: {str(e)}")
        return False
    finally:
        conn._io_lock.release()


def get_ableton_connection() -> AbletonConnection:
    """Get or create the persistent, shared Ableton connection."""
    global _ableton_connection

    with _connection_lock:
        if _existing_connection_alive():
            return _ableton_connection
        if _ableton_connection is not None:
            _ableton_connection.disconnect()
            _ableton_connection = None

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            logger.info(
                f"Connecting to Ableton at {ABLETON_HOST}:{ABLETON_PORT} "
                f"(attempt {attempt}/{max_attempts})..."
            )
            conn = AbletonConnection(host=ABLETON_HOST, port=ABLETON_PORT)
            if conn.connect():
                logger.info("Created new persistent connection to Ableton")
                _ableton_connection = conn
                return conn
            if attempt < max_attempts:
                time.sleep(1.0)

        logger.error("Failed to connect to Ableton after multiple attempts")
        raise AbletonNotConnectedError(_NOT_CONNECTED_MSG)


def disconnect_ableton() -> None:
    """Tear down the shared connection (used at server shutdown)."""
    global _ableton_connection
    with _connection_lock:
        if _ableton_connection:
            logger.info("Disconnecting from Ableton on shutdown")
            _ableton_connection.disconnect()
            _ableton_connection = None
