"""Socket connection to the AbletonMCP Remote Script (port 9877)."""

import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

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

_NOT_CONNECTED_MSG = (
    "Not connected to Ableton. Start Live, select AbletonMCP as the "
    "Control Surface (Settings > Link/Tempo/MIDI), and run "
    "`ableton-live-mcp doctor`."
)


class AbletonError(Exception):
    """Base class for everything this module raises."""


class AbletonNotConnectedError(AbletonError):
    """No connection to Live could be established."""


class AbletonTimeoutError(AbletonError):
    """Live did not answer within the command's budget."""


class AbletonConnectionLostError(AbletonError):
    """The socket died mid-conversation."""


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
        self._io_lock = threading.Lock()

    def connect(self) -> bool:
        """Connect to the Ableton Remote Script socket server"""
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
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Ableton: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, buffer_size=8192) -> dict[str, Any]:
        """Receive one complete JSON reply, potentially in multiple chunks,
        and return it parsed. The caller sets the socket timeout.

        Raises TimeoutError if Live stops sending before the frame completes and
        ConnectionError if the peer closes mid-frame, so send_command can map
        each to its own user-facing message.
        """
        chunks: list[bytes] = []
        while True:
            chunk = self.sock.recv(buffer_size)
            if not chunk:
                if not chunks:
                    raise ConnectionError("Connection closed before receiving any data")
                raise ConnectionError("Connection closed mid-response (incomplete JSON)")
            chunks.append(chunk)
            # The reply is one JSON object, so its final chunk must end with '}'
            # (modulo trailing whitespace); skip the parse attempt otherwise.
            if not chunk.rstrip().endswith(b"}"):
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

    def send_command(self, command_type: str, params: dict[str, Any] | None = None) -> Any:
        """Send a command to Ableton and return the response's result payload."""
        command = {"type": command_type, "params": params or {}}
        timeout = command_timeout(command_type, params)

        with self._io_lock:
            if not self.sock and not self.connect():
                raise AbletonNotConnectedError(_NOT_CONNECTED_MSG)
            try:
                logger.info(f"Sending command: {command_type}")
                logger.debug(f"Command params: {params}")
                # One deadline for the whole round trip, sendall included.
                self.sock.settimeout(timeout)
                self.sock.sendall(json.dumps(command).encode("utf-8"))
                response = self.receive_full_response()
            except TimeoutError:
                logger.error("Socket timeout while waiting for response from Ableton")
                self.disconnect()
                raise AbletonTimeoutError(
                    "Timeout waiting for Ableton response. If ALL commands time out, a "
                    "modal dialog is likely open in Live (it freezes the Remote Script) - "
                    "ask the user to dismiss it (press Enter in Live), then retry."
                ) from None
            except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                logger.error(f"Socket connection error: {str(e)}")
                self.disconnect()
                raise AbletonConnectionLostError(
                    f"Connection to Ableton lost ({e}). Is Live still running? "
                    "Run `ableton-live-mcp doctor` to diagnose."
                ) from None
            except Exception as e:
                logger.error(f"Error communicating with Ableton: {str(e)}")
                self.disconnect()
                raise AbletonError(f"Communication error with Ableton: {str(e)}") from e

        logger.debug(f"Response status: {response.get('status', 'unknown')}")
        if response.get("status") == "error":
            # Live executed the request and answered; the connection is fine.
            logger.error(f"Ableton error: {response.get('message')}")
            raise AbletonCommandError(response.get("message", "Unknown error from Ableton"))
        return response.get("result", {})


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
