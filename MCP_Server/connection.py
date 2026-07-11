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
ABLETON_PORT = int(os.environ.get("ABLETON_PORT", "9877"))

logger = logging.getLogger("AbletonMCPServer")

# Client-side socket timeouts. The Remote Script enforces its own main-thread
# queue budget (10 s default; see _COMMAND_TIMEOUTS in the Remote Script for
# long-running overrides) - the client budget must exceed it or the client can
# drop the socket at the same instant the script replies. Keep these two maps
# in sync; SOCKET_HEADROOM covers transport latency on top of the script budget.
REMOTE_DEFAULT_TIMEOUT = 10.0
REMOTE_COMMAND_TIMEOUTS = {"create_audio_clip": 60.0}
SOCKET_HEADROOM = 5.0


def command_timeout(command_type: str) -> float:
    """Client socket budget for one command: remote budget + transport headroom."""
    return REMOTE_COMMAND_TIMEOUTS.get(command_type, REMOTE_DEFAULT_TIMEOUT) + SOCKET_HEADROOM


@dataclass
class AbletonConnection:
    host: str
    port: int
    sock: socket.socket = None

    def __post_init__(self):
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
            self.sock = None
            return False

    def disconnect(self):
        """Disconnect from the Ableton Remote Script"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Ableton: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, timeout=15.0, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        sock.settimeout(timeout)

        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break

                    chunks.append(chunk)

                    # Check if we've received a complete JSON object
                    try:
                        data = b"".join(chunks)
                        json.loads(data.decode("utf-8"))
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except TimeoutError:
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise

        # If we get here, we either timed out or broke out of the loop
        if chunks:
            data = b"".join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                json.loads(data.decode("utf-8"))
                return data
            except json.JSONDecodeError:
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: dict[str, Any] = None) -> dict[str, Any]:
        """Send a command to Ableton and return the response"""
        command = {"type": command_type, "params": params or {}}

        try:
            with self._io_lock:
                if not self.sock and not self.connect():
                    raise ConnectionError(
                        "Not connected to Ableton. Start Live, select AbletonMCP as the "
                        "Control Surface (Settings > Link/Tempo/MIDI), and run `abletonmcp doctor`."
                    )
                logger.info(f"Sending command: {command_type} with params: {params}")
                self.sock.sendall(json.dumps(command).encode("utf-8"))
                logger.info("Command sent, waiting for response...")
                response_data = self.receive_full_response(
                    self.sock, timeout=command_timeout(command_type)
                )
            logger.info(f"Received {len(response_data)} bytes of data")

            # Parse the response
            response = json.loads(response_data.decode("utf-8"))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")

            if response.get("status") == "error":
                logger.error(f"Ableton error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Ableton"))

            return response.get("result", {})
        except TimeoutError:
            logger.error("Socket timeout while waiting for response from Ableton")
            self.sock = None
            raise Exception(
                "Timeout waiting for Ableton response. If ALL commands time out, a "
                "modal dialog is likely open in Live (it freezes the Remote Script) - "
                "ask the user to dismiss it (press Enter in Live), then retry."
            )
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(
                f"Connection to Ableton lost ({e}). Is Live still running? "
                "Run `abletonmcp doctor` to diagnose."
            )
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Ableton: {str(e)}")
            if "response_data" in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            self.sock = None
            raise Exception(f"Invalid response from Ableton: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Ableton: {str(e)}")
            self.sock = None
            raise Exception(f"Communication error with Ableton: {str(e)}")


# Global connection singleton (shared across all tool modules)
_ableton_connection = None


def get_ableton_connection():
    """Get or create a persistent Ableton connection"""
    global _ableton_connection

    if _ableton_connection is not None and _ableton_connection.sock is not None:
        try:
            # Check if the socket is still alive by peeking for data
            # MSG_PEEK + MSG_DONTWAIT will raise BlockingIOError if alive but no data,
            # or return b'' if the remote end has closed the connection.
            _ableton_connection.sock.setblocking(False)
            try:
                data = _ableton_connection.sock.recv(1, socket.MSG_PEEK)
                if data == b"":
                    raise ConnectionError("Remote end closed")
            except BlockingIOError:
                pass  # Socket is alive, just no data waiting - this is normal
            finally:
                _ableton_connection.sock.setblocking(True)
            return _ableton_connection
        except Exception as e:
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _ableton_connection.disconnect()
            except Exception:
                pass
            _ableton_connection = None

    # Connection doesn't exist or is invalid, create a new one
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(
                f"Connecting to Ableton at {ABLETON_HOST}:{ABLETON_PORT} "
                f"(attempt {attempt}/{max_attempts})..."
            )
            _ableton_connection = AbletonConnection(host=ABLETON_HOST, port=ABLETON_PORT)
            if _ableton_connection.connect():
                logger.info("Created new persistent connection to Ableton")
                return _ableton_connection
            _ableton_connection = None
        except Exception as e:
            logger.error(f"Connection attempt {attempt} failed: {str(e)}")
            if _ableton_connection:
                _ableton_connection.disconnect()
                _ableton_connection = None
        if attempt < max_attempts:
            time.sleep(1.0)

    logger.error("Failed to connect to Ableton after multiple attempts")
    raise Exception("Could not connect to Ableton. Make sure the Remote Script is running.")


def disconnect_ableton():
    """Tear down the shared connection (used at server shutdown)."""
    global _ableton_connection
    if _ableton_connection:
        logger.info("Disconnecting from Ableton on shutdown")
        try:
            _ableton_connection.disconnect()
        except Exception:
            pass
        _ableton_connection = None
