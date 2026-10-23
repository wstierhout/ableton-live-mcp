"""AbletonMCP Remote Script: the Live-side half of the server.

This module runs inside Ableton Live (imported by Live's Remote Script host, not
by the MCP server). `ableton-live-mcp install` copies this file into the User
Library as Remote Scripts/AbletonMCP/__init__.py. It opens a loopback socket on
port 9877 and dispatches JSON commands to the Live API on the main thread.
"""

import codecs
import json
import math
import os
import queue
import re
import socket
import threading
import time
import traceback
import uuid
from collections import OrderedDict, deque

try:
    from hashlib import sha256 as _sha256
except ImportError:
    _sha256 = None


from _Framework.ControlSurface import ControlSurface

# Socket the script listens on. Bind loopback only by default - this port drives
# Live with no auth, so it must not be exposed to the network. If 9877 is taken,
# set ABLETON_MCP_PORT (and the client's ABLETON_PORT to match). ABLETON_MCP_HOST
# overrides the bind address for advanced setups (e.g. containers) at your own risk.
DEFAULT_PORT = int(os.environ.get("ABLETON_MCP_PORT", "9877"))
HOST = os.environ.get("ABLETON_MCP_HOST", "127.0.0.1")
MAX_REQUEST_BYTES = 10 * 1024 * 1024  # backstop against a poisoned request buffer
BRIDGE_VERSION = "1.8.1"
BRIDGE_PROTOCOL = 2


class CommandFailure(Exception):
    """An application failure with a machine-readable account of completed work."""
    def __init__(self, message, details):
        Exception.__init__(self, message)
        self.details = details


class _Deferred:
    """A continuation started on Live's main thread; never wait on that thread."""
    def __init__(self, start):
        self.start = start


class _BatchProbe:
    """Evaluate dispatch argument binding without touching any Live objects."""
    def _req(self, params, key):
        if key not in params:
            raise ValueError("Missing required parameter: " + key)
        return params[key]

    def __getattr__(self, name):
        if not name.startswith("_") and name not in ("get_browser_tree", "get_browser_items_at_path"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


class _ProbeParams(dict):
    def __init__(self, values):
        dict.__init__(self, values)
        self.consumed = set()

    def get(self, key, default=None):
        self.consumed.add(key)
        return dict.get(self, key, default)

    def __getitem__(self, key):
        self.consumed.add(key)
        return dict.__getitem__(self, key)


def _request_fingerprint(command_type, params):
    encoded = json.dumps([command_type, params], sort_keys=True).encode("utf-8")
    if _sha256 is not None:
        try:
            return _sha256(encoded).digest()
        except (ValueError, RuntimeError):
            pass
    # Some embedded runtimes omit crypto modules. Small requests can still be
    # compared exactly; large opaque requests must never be replayed blindly.
    return encoded if len(encoded) <= 32768 else None


def _fits_receipt(value, byte_limit=65536, node_limit=1024):
    """Conservative JSON-size bound, with strictly bounded main-thread work.

    Actual JSON serialization happens once, on the socket thread. Twelve bytes
    per character bounds ensure_ascii's worst case (surrogate-pair escaping).
    """
    remaining = [byte_limit, node_limit]
    def visit(item, depth):
        remaining[1] -= 1
        if remaining[1] < 0 or depth > 24:
            return False
        if isinstance(item, str):
            cost = 12 * len(item) + 2
        elif item is None or isinstance(item, (bool, float)):
            cost = 32
        elif isinstance(item, int):
            cost = max(32, item.bit_length() + 2)
        elif isinstance(item, (dict, list, tuple)):
            if len(item) > remaining[1]:
                return False
            remaining[0] -= 2 + len(item) * (4 if isinstance(item, dict) else 2)
            if isinstance(item, dict):
                for key, val in item.items():
                    if not visit(key, depth + 1) or not visit(val, depth + 1):
                        return False
            else:
                for val in item:
                    if not visit(val, depth + 1):
                        return False
            return remaining[0] >= 0
        else:
            return False
        remaining[0] -= cost
        return remaining[0] >= 0
    return visit(value, 0)


def _display_number(text):
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(Hz|kHz|ms|s|dB|%)\s*", str(text), re.I)
    if not match:
        raise ValueError("Use a native number, exact enum label, or Hz/kHz/ms/s/dB/% display text")
    value, unit = float(match.group(1)), match.group(2).lower()
    if unit == "khz":
        value, unit = value * 1000, "hz"
    elif unit == "s":
        value, unit = value * 1000, "ms"
    if not math.isfinite(value):
        raise ValueError("Display value must be finite")
    return value, unit


def _parameter_value(param, requested):
    """Resolve display text without speculative writes; reject ambiguous mappings."""
    low, high = float(param.min), float(param.max)
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        raise ValueError("Invalid parameter range")
    if not isinstance(requested, str):
        raw = float(requested)
        if not math.isfinite(raw):
            raise ValueError("Parameter value must be finite")
        clamped = max(low, min(high, raw))
        if param.is_quantized:
            clamped = max(low, min(high, math.floor(clamped + 0.5)))
        return clamped, "clamped or quantized to native range" if clamped != raw else None
    if param.is_quantized:
        if high - low > 512:
            raise ValueError("Enum range too large; use a native numeric value")
        matches = [v for v in range(math.ceil(low), math.floor(high) + 1)
                   if param.str_for_value(v).strip().casefold() == requested.strip().casefold()]
        if len(matches) != 1:
            raise ValueError("Display label is missing or ambiguous; use a native value")
        return matches[0], None
    target, unit = _display_number(requested)
    samples = []
    for i in range(9):
        raw = low + (high - low) * i / 8
        display, found_unit = _display_number(param.str_for_value(raw))
        if found_unit != unit:
            raise ValueError("Requested unit does not match this parameter")
        samples.append(display)
    ascending = samples[-1] > samples[0]
    if samples[-1] == samples[0] or any(
        (b < a if ascending else b > a) for a, b in zip(samples, samples[1:])
    ):
        raise ValueError("Display mapping is not monotonic; use a native numeric value")
    if not min(samples) <= target <= max(samples):
        raise ValueError("Display value is outside the parameter range; no change made")
    candidates = []
    for _ in range(32):
        raw = (low + high) / 2
        display, found_unit = _display_number(param.str_for_value(raw))
        if found_unit != unit:
            raise ValueError("Display unit changes across the range; use a native value")
        candidates.append((abs(display - target), raw))
        if (display < target) == ascending:
            low = raw
        else:
            high = raw
    error, raw = min(candidates)
    if error > max(abs(target) * 0.005, 0.01):
        raise ValueError("Cannot resolve requested display value reliably; use a native value")
    return raw, "display conversion is approximate; check returned display"

def create_instance(c_instance):
    """Create and return the AbletonMCP script instance"""
    return AbletonMCP(c_instance)

class AbletonMCP(ControlSurface):
    """AbletonMCP Remote Script for Ableton Live"""

    def __init__(self, c_instance):
        """Initialize the control surface"""
        ControlSurface.__init__(self, c_instance)
        self.log_message("AbletonMCP Remote Script initializing...")

        # Socket server for communication
        self.server = None
        self.client_threads = []
        self.client_sockets = []
        self.server_thread = None
        self.running = False
        # __init__ runs on Live's main thread; used to detect direct dispatch.
        self._main_thread = threading.current_thread()
        self._receipts = OrderedDict()
        self._receipt_lock = threading.Lock()

        # Cache the song reference for easier access
        self._song = self.song()

        # Start the socket server
        self.start_server()

        self.log_message("AbletonMCP initialized")

        # Show a message in Ableton (only if the server actually started -
        # start_server shows its own error otherwise).
        if self.running:
            self.show_message("AbletonMCP: Listening for commands on port " + str(DEFAULT_PORT))

    def disconnect(self):
        """Called when Ableton closes or the control surface is removed"""
        self.log_message("AbletonMCP disconnecting...")
        self.running = False

        # Stop the server
        if self.server:
            try:
                self.server.close()
            except:
                pass

        # Close client sockets so threads blocked in recv() wake up and exit;
        # otherwise a reloaded script leaves the old thread serving a stale song.
        for client_sock in self.client_sockets[:]:
            try:
                client_sock.close()
            except Exception:
                pass
        self.client_sockets = []

        # Wait for the server thread to exit
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(1.0)

        # Clean up any client threads
        for client_thread in self.client_threads[:]:
            if client_thread.is_alive():
                # We don't join them as they might be stuck
                self.log_message("Client thread still alive during disconnect")

        ControlSurface.disconnect(self)
        self.log_message("AbletonMCP disconnected")

    def start_server(self):
        """Start the socket server in a separate thread"""
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind((HOST, DEFAULT_PORT))
            self.server.listen(5)  # Allow up to 5 pending connections

            self.running = True
            self.server_thread = threading.Thread(target=self._server_thread)
            self.server_thread.daemon = True
            self.server_thread.start()

            self.log_message("Server started on port " + str(DEFAULT_PORT))
        except Exception as e:
            self.log_message("Error starting server: " + str(e))
            self.show_message("AbletonMCP: Error starting server - " + str(e))

    def _server_thread(self):
        """Server thread implementation - handles client connections"""
        try:
            self.log_message("Server thread started")
            # Set a timeout to allow regular checking of running flag
            self.server.settimeout(1.0)

            while self.running:
                try:
                    # Accept connections with timeout
                    client, address = self.server.accept()
                    self.log_message("Connection accepted from " + str(address))
                    self.show_message("AbletonMCP: Client connected")
                    self.client_sockets.append(client)

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()

                    # Keep track of client threads
                    self.client_threads.append(client_thread)

                    # Clean up finished client threads
                    self.client_threads = [t for t in self.client_threads if t.is_alive()]

                except socket.timeout:  # noqa: UP041 - Live 11 embeds Python 3.7, where socket.timeout is not yet a TimeoutError alias
                    # No connection yet, just continue
                    continue
                except Exception as e:
                    if self.running:  # Only log if still running
                        self.log_message("Server accept error: " + str(e))
                    time.sleep(0.5)

            self.log_message("Server thread stopped")
        except Exception as e:
            self.log_message("Server thread error: " + str(e))

    def _send_response(self, client, response):
        """Serialize and send one response, surviving unserializable results."""
        try:
            payload = json.dumps(response)
        except (TypeError, ValueError) as e:
            self.log_message("Unserializable handler result: " + str(e))
            payload = json.dumps({"status": "error",
                                  "message": "Handler returned an unserializable result: " + str(e)})
        client.sendall(payload.encode("utf-8"))

    def _handle_client(self, client):
        """Handle communication with a connected client"""
        self.log_message("Client handler started")
        client.settimeout(None)  # No timeout for client socket
        decoder = json.JSONDecoder()
        # An 8192-byte read can split a multibyte UTF-8 character; the
        # incremental decoder holds the partial bytes until the rest arrives,
        # at linear cost (no whole-buffer re-decode per chunk).
        utf8 = codecs.getincrementaldecoder("utf-8")()
        buffer = ""

        try:
            while self.running:
                try:
                    # Receive data
                    data = client.recv(8192)

                    if not data:
                        # Client disconnected
                        self.log_message("Client disconnected")
                        break

                    try:
                        buffer += utf8.decode(data)
                    except UnicodeDecodeError:
                        self._send_response(client, {"status": "error",
                                                     "message": "Request is not valid UTF-8; buffer cleared"})
                        utf8.reset()
                        buffer = ""
                        continue
                    if len(buffer) > MAX_REQUEST_BYTES:
                        self._send_response(client, {"status": "error",
                                                     "message": "Request too large; buffer cleared"})
                        buffer = ""
                        continue

                    # Parse as many complete JSON objects as the buffer holds,
                    # keeping any trailing partial text. raw_decode tolerates
                    # coalesced messages and needs no delimiter, so it stays
                    # compatible with single-object clients.
                    buffer = buffer.lstrip()
                    while buffer:
                        try:
                            command, end = decoder.raw_decode(buffer)
                        except ValueError:
                            # Incomplete object: wait for more bytes. A buffer
                            # that cannot even start a JSON object will never
                            # parse - answer and drop it instead of wedging the
                            # connection forever.
                            if not buffer.startswith(("{", "[")):
                                self._send_response(client, {"status": "error",
                                                             "message": "Invalid JSON request; buffer cleared"})
                                buffer = ""
                            break
                        buffer = buffer[end:].lstrip()
                        if not isinstance(command, dict):
                            self._send_response(client, {"status": "error",
                                                         "message": "Request must be a JSON object"})
                            continue
                        self.log_message("Received command: " + str(command.get("type", "unknown")))
                        response = self._process_command(command)
                        self._send_response(client, response)

                except Exception as e:
                    self.log_message("Error handling client data: " + str(e))
                    self.log_message(traceback.format_exc())

                    # Send error response if possible
                    error_response = {
                        "status": "error",
                        "message": str(e)
                    }
                    try:
                        client.sendall(json.dumps(error_response).encode("utf-8"))
                    except:
                        # If we can't send the error, the connection is probably dead
                        break

                    # For serious errors, break the loop
                    if not isinstance(e, ValueError):
                        break
        except Exception as e:
            self.log_message("Error in client handler: " + str(e))
        finally:
            try:
                client.close()
            except:
                pass
            if client in self.client_sockets:
                self.client_sockets.remove(client)
            self.log_message("Client handler stopped")

    @staticmethod
    def _req(p, key):
        """Fetch a required command parameter; fail fast instead of defaulting."""
        if key not in p:
            raise Exception(f"Missing required parameter: {key}")
        return p[key]

    # ── Declarative command dispatch ─────────────────────────────────
    # ALL commands (mutating and read-only) are marshalled onto Live's main
    # thread by _process_command; the two dicts only document intent.
    _MUTATING_COMMANDS = {
        "create_midi_track": lambda s, p: s._create_midi_track(p.get("index", -1)),
        "create_audio_track": lambda s, p: s._create_audio_track(p.get("index", -1)),
        "create_return_track": lambda s, p: s._create_return_track(),
        "delete_track": lambda s, p: s._delete_track(s._req(p, "track_index")),
        "duplicate_track": lambda s, p: s._duplicate_track(s._req(p, "track_index")),
        "set_track_name": lambda s, p: s._set_track_name(s._req(p, "track_index"), s._req(p, "name")),
        "set_track_color": lambda s, p: s._set_track_color(s._req(p, "track_index"), p.get("color_index", 0)),
        "set_track_volume": lambda s, p: s._set_track_volume(s._req(p, "track_index"), s._req(p, "volume")),
        "set_track_pan": lambda s, p: s._set_track_pan(s._req(p, "track_index"), s._req(p, "pan")),
        "set_track_mute": lambda s, p: s._set_track_mute(s._req(p, "track_index"), p.get("mute", False)),
        "set_track_solo": lambda s, p: s._set_track_solo(s._req(p, "track_index"), p.get("solo", False)),
        "set_master_volume": lambda s, p: s._set_master_volume(s._req(p, "volume")),
        "set_send": lambda s, p: s._set_send(s._req(p, "track_index"), s._req(p, "send_index"), s._req(p, "value")),
        "create_clip": lambda s, p: s._create_clip(s._req(p, "track_index"), s._req(p, "clip_index"), p.get("length", 4.0)),
        "create_audio_clip": lambda s, p: s._create_audio_clip(s._req(p, "track_index"), s._req(p, "clip_index"), s._req(p, "path")),
        "delete_clip": lambda s, p: s._delete_clip(s._req(p, "track_index"), s._req(p, "clip_index")),
        "add_notes_to_clip": lambda s, p: s._add_notes_to_clip(s._req(p, "track_index"), s._req(p, "clip_index"), s._req(p, "notes")),
        "edit_notes": lambda s, p: s._edit_notes(s._req(p, "track_index"), s._req(p, "clip_index"), p.get("add", []), p.get("remove", [])),
        "set_clip_name": lambda s, p: s._set_clip_name(s._req(p, "track_index"), s._req(p, "clip_index"), s._req(p, "name")),
        "set_clip_color": lambda s, p: s._set_clip_color(s._req(p, "track_index"), s._req(p, "clip_index"), p.get("color_index", 0)),
        "set_clip_groove": lambda s, p: s._set_clip_groove(s._req(p, "track_index"), s._req(p, "clip_index"), p.get("groove_index")),
        "set_clip_loop": lambda s, p: s._set_clip_loop(s._req(p, "track_index"), s._req(p, "clip_index"), p),
        "set_clip_audio": lambda s, p: s._set_clip_audio(s._req(p, "track_index"), s._req(p, "clip_index"), p),
        "quantize_clip": lambda s, p: s._quantize_clip(s._req(p, "track_index"), s._req(p, "clip_index"), p.get("grid", "sixteenth"), p.get("amount", 1.0)),
        "fire_clip": lambda s, p: s._fire_clip(s._req(p, "track_index"), s._req(p, "clip_index")),
        "stop_clip": lambda s, p: s._stop_clip(s._req(p, "track_index"), s._req(p, "clip_index")),
        "fire_scene": lambda s, p: s._fire_scene(s._req(p, "scene_index")),
        "set_scene_name": lambda s, p: s._set_scene_name(s._req(p, "scene_index"), s._req(p, "name")),
        "create_scene": lambda s, p: s._create_scene(p.get("index", -1)),
        "set_tempo": lambda s, p: s._set_tempo(s._req(p, "tempo")),
        "set_time_signature": lambda s, p: s._set_time_signature(p.get("numerator", 4), p.get("denominator", 4)),
        "set_loop": lambda s, p: s._set_loop(p.get("start", 0.0), p.get("length", 16.0), p.get("enabled", True)),
        "start_playback": lambda s, p: s._start_playback(),
        "stop_playback": lambda s, p: s._stop_playback(),
        "undo": lambda s, p: s._undo(),
        "redo": lambda s, p: s._redo(),
        "create_locator": lambda s, p: s._create_locator(p.get("name"), p.get("time")),
        "load_browser_item": lambda s, p: s._load_browser_item(s._req(p, "track_index"), s._req(p, "item_uri")),
        "load_device_to_return": lambda s, p: s._load_device_to_return(s._req(p, "return_index"), s._req(p, "item_uri")),
        "load_device_to_master": lambda s, p: s._load_device_to_master(s._req(p, "item_uri")),
        "set_device_parameter": lambda s, p: s._set_device_parameter(s._req(p, "track_index"), s._req(p, "device_index"), s._req(p, "parameter"), s._req(p, "value")),
        "set_return_device_parameter": lambda s, p: s._set_device_parameter(s._req(p, "return_index"), s._req(p, "device_index"), s._req(p, "parameter"), s._req(p, "value"), track_type="return"),
        "set_master_device_parameter": lambda s, p: s._set_device_parameter(0, s._req(p, "device_index"), s._req(p, "parameter"), s._req(p, "value"), track_type="master"),
        "set_device_enabled": lambda s, p: s._set_device_enabled(s._req(p, "track_index"), s._req(p, "device_index"), p.get("enabled", True)),
        "write_automation": lambda s, p: s._write_automation(s._req(p, "track_index"), s._req(p, "clip_index"), s._req(p, "device_index"), s._req(p, "parameter"), s._req(p, "points")),
        "clear_automation": lambda s, p: s._clear_automation(s._req(p, "track_index"), s._req(p, "clip_index"), s._req(p, "device_index"), s._req(p, "parameter")),
        "switch_to_arrangement_view": lambda s, p: s._switch_to_arrangement_view(),
        "set_current_song_time": lambda s, p: s._set_current_song_time(p.get("time", 0.0)),
        "duplicate_session_clip_to_arrangement": lambda s, p: s._duplicate_session_clip_to_arrangement(s._req(p, "track_index"), s._req(p, "clip_index"), s._req(p, "destination_time")),
        "delete_arrangement_clip": lambda s, p: s._delete_arrangement_clip(s._req(p, "track_index"), s._req(p, "arrangement_clip_index")),
        "batch": lambda s, p: s._batch(p.get("commands", [])),
        "set_track_arm": lambda s, p: s._set_track_arm(s._req(p, "track_index"), p.get("arm", True)),
        "capture_midi": lambda s, p: s._capture_midi(),
        "set_song_scale": lambda s, p: s._set_song_scale(p.get("root_note"), p.get("scale_name")),
        "set_metronome": lambda s, p: s._set_metronome(p.get("enabled", True)),
        "stop_all_clips": lambda s, p: s._stop_all_clips(),
        "back_to_arranger": lambda s, p: s._back_to_arranger(),
        "set_record_mode": lambda s, p: s._set_record_mode(p.get("enabled", False)),
        "set_session_record": lambda s, p: s._set_session_record(p.get("enabled", False)),
        "continue_playing": lambda s, p: s._continue_playing(),
        "set_clip_trigger_quantization": lambda s, p: s._set_clip_trigger_quantization(s._req(p, "value")),
        "delete_scene": lambda s, p: s._delete_scene(s._req(p, "scene_index")),
        "duplicate_scene": lambda s, p: s._duplicate_scene(s._req(p, "scene_index")),
        "delete_return_track": lambda s, p: s._delete_return_track(s._req(p, "return_index")),
        "capture_and_insert_scene": lambda s, p: s._capture_and_insert_scene(),
        "jump_to_locator": lambda s, p: s._jump_to_locator(s._req(p, "locator_index")),
        "re_enable_automation": lambda s, p: s._re_enable_automation(),
        "set_arrangement_overdub": lambda s, p: s._set_arrangement_overdub(p.get("enabled", False)),
        "set_session_automation_record": lambda s, p: s._set_session_automation_record(p.get("enabled", False)),
        "trigger_session_record": lambda s, p: s._trigger_session_record(p.get("record_length")),
        "duplicate_clip_to": lambda s, p: s._duplicate_clip_to(s._req(p, "src_track"), s._req(p, "src_scene"), s._req(p, "dst_track"), s._req(p, "dst_scene")),
        "set_track_routing": lambda s, p: s._set_track_routing(s._req(p, "track_index"), s._req(p, "field"), s._req(p, "display_name")),
        "set_track_monitoring": lambda s, p: s._set_track_monitoring(s._req(p, "track_index"), s._req(p, "state")),
        "clip_op": lambda s, p: s._clip_op(s._req(p, "track_index"), s._req(p, "clip_index"), s._req(p, "op"), p.get("params")),
        "set_clip_signature": lambda s, p: s._set_clip_signature(s._req(p, "track_index"), s._req(p, "clip_index"), s._req(p, "numerator"), s._req(p, "denominator")),
        "set_chain_device_parameter": lambda s, p: s._set_chain_device_parameter(s._req(p, "track_index"), s._req(p, "device_index"), s._req(p, "chain_index"), s._req(p, "chain_device_index"), s._req(p, "parameter"), s._req(p, "value")),
        "set_drum_pad": lambda s, p: s._set_drum_pad(s._req(p, "track_index"), s._req(p, "device_index"), s._req(p, "note"), {k: p[k] for k in ("mute", "solo", "name") if k in p}),
        "rack_variation": lambda s, p: s._rack_variation(s._req(p, "track_index"), s._req(p, "device_index"), s._req(p, "action"), p.get("index")),
        "set_crossfader": lambda s, p: s._set_crossfader(s._req(p, "value")),
        "set_crossfade_assign": lambda s, p: s._set_crossfade_assign(s._req(p, "track_index"), s._req(p, "assign")),
        "tap_tempo": lambda s, p: s._tap_tempo(),
        "set_groove_amount": lambda s, p: s._set_groove_amount(s._req(p, "amount")),
        "set_swing_amount": lambda s, p: s._set_swing_amount(s._req(p, "amount")),
        "jump_by": lambda s, p: s._jump_by(s._req(p, "beats")),
        "jump_to_cue": lambda s, p: s._jump_to_cue(p.get("direction", 1)),
        "set_ableton_link": lambda s, p: s._set_ableton_link(p.get("enabled", True)),
        "delete_device": lambda s, p: s._delete_device(s._req(p, "track_index"), s._req(p, "device_index")),
        "create_take_lane": lambda s, p: s._create_take_lane(s._req(p, "track_index")),
        "set_simpler_playback_mode": lambda s, p: s._set_simpler_playback_mode(s._req(p, "track_index"), s._req(p, "device_index"), s._req(p, "mode")),
        "replace_simpler_sample": lambda s, p: s._replace_simpler_sample(s._req(p, "track_index"), s._req(p, "device_index"), s._req(p, "path")),
        "set_fold_state": lambda s, p: s._set_fold_state(s._req(p, "track_index"), p.get("folded", True)),
        "try_save_project": lambda s, p: s._try_save_project(),
        "set_device_routing": lambda s, p: s._set_device_routing(s._req(p, "track_index"), s._req(p, "device_index"), s._req(p, "field"), s._req(p, "display_name")),
        "preview_browser_item": lambda s, p: s._preview_browser_item(s._req(p, "item_uri")),
        "stop_browser_preview": lambda s, p: s._stop_browser_preview(),
    }

    _READONLY_COMMANDS = {
        "get_session_notes": lambda s, p: s._get_session_notes(),
        "get_operation_status": lambda s, p: s._get_operation_status(s._req(p, "operation_id")),
        "get_session_info": lambda s, p: s._get_session_info(),
        "get_track_info": lambda s, p: s._get_track_info(s._req(p, "track_index")),
        "get_return_tracks": lambda s, p: s._get_return_tracks(),
        "get_arrangement_clips": lambda s, p: s._get_arrangement_clips(s._req(p, "track_index")),
        "get_clip_notes": lambda s, p: s._get_clip_notes(s._req(p, "track_index"), s._req(p, "clip_index")),
        "get_grooves": lambda s, p: s._get_grooves(),
        "get_device_parameters": lambda s, p: s._get_device_parameters(s._req(p, "track_index"), s._req(p, "device_index")),
        "get_return_device_parameters": lambda s, p: s._get_device_parameters(s._req(p, "return_index"), s._req(p, "device_index"), track_type="return"),
        "get_master_device_parameters": lambda s, p: s._get_device_parameters(0, s._req(p, "device_index"), track_type="master"),
        "get_browser_tree": lambda s, p: s.get_browser_tree(p.get("category_type", "all")),
        "get_browser_items_at_path": lambda s, p: s.get_browser_items_at_path(p.get("path", "")),
        "search_browser": lambda s, p: s._search_browser(p.get("query", ""), p.get("category"), p.get("max_results", 25)),
        "get_track_meters": lambda s, p: s._get_track_meters(s._req(p, "track_index")),
        "get_master_meters": lambda s, p: s._get_master_meters(),
        "get_locators": lambda s, p: s._get_locators(),
        "get_track_routing": lambda s, p: s._get_track_routing(s._req(p, "track_index")),
        "get_clip_info": lambda s, p: s._get_clip_info(s._req(p, "track_index"), s._req(p, "clip_index")),
        "get_rack_chains": lambda s, p: s._get_rack_chains(s._req(p, "track_index"), s._req(p, "device_index")),
        "get_drum_pads": lambda s, p: s._get_drum_pads(s._req(p, "track_index"), s._req(p, "device_index")),
        "get_session_snapshot": lambda s, p: s._get_session_snapshot(),
        "get_group_info": lambda s, p: s._get_group_info(s._req(p, "track_index")),
        "get_device_routing": lambda s, p: s._get_device_routing(s._req(p, "track_index"), s._req(p, "device_index")),
        "get_scale_info": lambda s, p: s._get_scale_info(),
    }

    # One merged lookup view; the two source dicts document intent.
    _ALL_COMMANDS = {**_MUTATING_COMMANDS, **_READONLY_COMMANDS}

    # Commands whose main-thread work can exceed the default 10 s budget.
    _COMMAND_TIMEOUTS = {"create_audio_clip": 60.0}

    def _process_command(self, command):
        """Socket-thread entry: queue work, record its outcome, never wait in Live."""
        if threading.current_thread() is self._main_thread:
            return {"status": "error", "message": "Socket dispatch cannot wait on Live's main thread"}
        if not isinstance(command, dict) or not isinstance(command.get("params", {}), dict):
            return {"status": "error", "message": "Command and params must be objects"}
        command_type = command.get("type", "")
        params = command.get("params", {})
        handler = self._ALL_COMMANDS.get(command_type) if isinstance(command_type, str) else None
        if handler is None:
            return {"status": "error", "message": "Unknown command: " + str(command_type)}
        operation_id = command.get("operation_id") or uuid.uuid4().hex
        if not isinstance(operation_id, str) or len(operation_id) > 128:
            return {"status": "error", "message": "Invalid operation_id"}
        mutating = command_type in self._MUTATING_COMMANDS
        fingerprint = _request_fingerprint(command_type, params) if mutating else None
        receipt = {"state": "queued", "fingerprint": fingerprint, "type": command_type}
        if mutating:
            with self._receipt_lock:
                previous = self._receipts.get(operation_id)
                if previous:
                    if fingerprint is None or previous["fingerprint"] != fingerprint:
                        return {"status": "error", "operation_id": operation_id,
                                "message": "operation_id already belongs to another command, or its arguments cannot be verified; inspect its receipt"}
                    if previous["state"] != "cancelled_before_start":
                        return previous.get("response") or {
                            "status": "error", "message": "Operation already submitted; inspect its receipt before retrying",
                            "operation_id": operation_id, "details": {"state": previous["state"]}}
                    # Allocate a NEW attempt. The old queued callback must keep
                    # its cancelled receipt and can never observe this as queued.
                    del self._receipts[operation_id]
                while len(self._receipts) >= 256:
                    old = next((key for key, value in self._receipts.items() if value["state"] not in ("queued", "running")), None)
                    if old is None:
                        return {"status": "error", "message": "Too many pending operations"}
                    del self._receipts[old]
                self._receipts[operation_id] = receipt
        response_queue = queue.Queue()

        def finish(result=None, error=None):
            response = {"operation_id": operation_id, "status": "success", "result": result}
            if error is not None:
                response = {"operation_id": operation_id, "status": "error", "message": str(error),
                            "details": dict({"may_have_applied": mutating}, **getattr(error, "details", {}))}
            state = "failed" if error is not None else "completed"
            retained = None
            if mutating:
                retained = response if _fits_receipt(response) else {
                    "operation_id": operation_id, "status": "error",
                    "message": f"Operation {state}; detailed result exceeds receipt retention limits. Inspect current state before another edit.",
                    "details": {"state": state, "result_retained": False}}
            with self._receipt_lock:
                receipt["state"] = state
                receipt["response"] = retained if mutating else response
            response_queue.put(response)

        def main_thread_task():
            with self._receipt_lock:
                if receipt["state"] == "cancelled_before_start":
                    return
                receipt["state"] = "running"
            try:
                guarded = self._guard_target(params)
                result = handler(self, guarded)
                if isinstance(result, _Deferred):
                    result.start(finish)
                else:
                    finish(result=result)
            except Exception as exc:
                self.log_message(traceback.format_exc())
                finish(error=exc)

        self.schedule_message(0, main_thread_task)
        timeout = self._COMMAND_TIMEOUTS.get(command_type, 10.0)
        if command_type == "batch" and isinstance(params.get("commands"), list):
            for cmd in params.get("commands") or []:
                sub = cmd.get("type", "") if isinstance(cmd, dict) else ""
                timeout = max(timeout, self._COMMAND_TIMEOUTS.get(self._BATCH_ALIASES.get(sub, sub), 0.0))
        try:
            return response_queue.get(timeout=timeout)
        except queue.Empty:
            with self._receipt_lock:
                if receipt.get("response"):
                    return receipt["response"]
                queued = receipt["state"] == "queued"
                if queued:
                    receipt["state"] = "cancelled_before_start"
                state = receipt["state"]
            return {"status": "error", "operation_id": operation_id,
                    "message": ("Timed out before starting; operation cancelled and will not apply late. A modal dialog may block Live."
                                if queued else "Timed out after starting; outcome is uncertain. Inspect the operation receipt and Live state before retrying."),
                    "details": {"state": state, "may_have_applied": mutating and not queued}}

    def _get_operation_status(self, operation_id):
        with self._receipt_lock:
            entry = self._receipts.get(operation_id)
            if not entry:
                return {"operation_id": operation_id, "state": "unknown_or_expired", "safe_to_retry": False}
            return {"operation_id": operation_id, "state": entry["state"], "response": entry.get("response")}

    def _guard_target(self, params):
        """Optional optimistic guards; check identity immediately before an edit."""
        params = dict(params)
        recording_owner = params.pop("expected_recording_track_id", None)
        if recording_owner is not None:
            tracks = list(self._song.tracks)
            identities = self._snapshot_track_ids(tracks)
            if recording_owner not in identities or any(
                    track.can_be_armed and track.arm and identity != recording_owner
                    for track, identity in zip(tracks, identities)):
                raise ValueError("Recording ownership changed; global transport was left untouched")
        expected = params.pop("expected_track_id", None)
        if expected is not None:
            identities = self._snapshot_track_ids(list(self._song.tracks))
            index = params.get("track_index")
            if type(index) is not int or not 0 <= index < len(identities) or identities[index] != expected:
                raise ValueError("Stale track target: refresh the session snapshot before editing")
        return params

    def _later_readback(self, read, matches, requested, timeout=2.0):
        """Poll on subsequent Live ticks, bounded by monotonic elapsed time."""
        def start(finish):
            deadline = time.monotonic() + timeout
            def poll():
                try:
                    observed = read()
                    confirmed = bool(matches(observed))
                    if confirmed or time.monotonic() >= deadline:
                        result = dict(observed)
                        result.update({"requested": requested, "observed": observed,
                                       "confirmed": confirmed,
                                       "status": "confirmed" if confirmed else "requested_unconfirmed"})
                        finish(result=result)
                    else:
                        self.schedule_message(1, poll)
                except Exception as exc:
                    finish(error=exc)
            self.schedule_message(1, poll)
        return _Deferred(start)

    # Command implementations

    def _safe_song_property(self, attr, cast, default):
        """Read self._song.<attr> with cast, returning default on common failures.
        Catches only narrow exceptions so genuine bugs still surface."""
        try:
            return cast(getattr(self._song, attr))
        except (AttributeError, TypeError, ValueError):
            return default

    def _live_version(self):
        if not hasattr(self, "_live_version_cache"):
            app = self.application()
            self._live_version_cache = f"{app.get_major_version()}.{app.get_minor_version()}.{app.get_bugfix_version()}"
        return self._live_version_cache

    def _get_session_info(self):
        """Get information about the current session"""
        result = {
            "tempo": self._song.tempo,
            "signature_numerator": self._song.signature_numerator,
            "signature_denominator": self._song.signature_denominator,
            "track_count": len(self._song.tracks),
            "return_track_count": len(self._song.return_tracks),
            "master_track": {
                "name": "Master",
                "volume": self._song.master_track.mixer_device.volume.value,
                "panning": self._song.master_track.mixer_device.panning.value
            },
            # Transport / playback state - lets clients render a live
            # playhead without polling separately. Each property is read
            # via _safe_song_property so an attribute missing on a given
            # Live version falls back to its default rather than breaking
            # the response shape.
            "is_playing":        self._safe_song_property("is_playing",        bool,  False),
            "current_song_time": self._safe_song_property("current_song_time", float, 0.0),
            "song_length":       self._safe_song_property("song_length",       float, 0.0),
            "loop":              self._safe_song_property("loop",              bool,  False),
            "loop_start":        self._safe_song_property("loop_start",        float, 0.0),
            "loop_length":       self._safe_song_property("loop_length",       float, 0.0),
            "scene_count": len(self._song.scenes),
            "scene_names": [sc.name for i, sc in enumerate(self._song.scenes) if i < 64],
            "root_note":         self._safe_song_property("root_note",         int,   0),
            "scale_name":        self._safe_song_property("scale_name",        str,   ""),
            "record_mode":       self._safe_song_property("record_mode",       bool,  False),
            "clip_trigger_quantization": self._safe_song_property("clip_trigger_quantization", int, 4),
            "live_version": self._live_version(),
            "bridge_version": BRIDGE_VERSION,
            "bridge_protocol": BRIDGE_PROTOCOL,
            "capabilities": ["batch_preflight", "confirmed_transport", "confirmed_locator",
                             "chain_display_values", "operation_receipts", "stable_track_guards", "bulk_session_notes"],
        }
        return result

    def _get_track_info(self, track_index):
        """Get information about a track"""
        track = self._get_track(track_index)

        # Get clip slots
        clip_slots = []
        for slot_index, slot in enumerate(track.clip_slots):
            clip_info = None
            if slot.has_clip:
                clip = slot.clip
                clip_info = {
                    "name": clip.name,
                    "length": clip.length,
                    "is_playing": clip.is_playing,
                    "is_recording": clip.is_recording
                }

            clip_slots.append({
                "index": slot_index,
                "has_clip": slot.has_clip,
                "clip": clip_info
            })

        # Get devices
        devices = []
        for device_index, device in enumerate(track.devices):
            devices.append({
                "index": device_index,
                "name": device.name,
                "class_name": device.class_name,
                "type": self._get_device_type(device)
            })

        result = {
            "index": track_index,
            "name": track.name,
            "is_audio_track": track.has_audio_input,
            "is_midi_track": track.has_midi_input,
            "mute": track.mute,
            "solo": track.solo,
            "arm": track.arm if track.can_be_armed else None,
            "volume": track.mixer_device.volume.value,
            "panning": track.mixer_device.panning.value,
            "sends": [s.value for s in track.mixer_device.sends],
            "playing_slot_index": getattr(track, "playing_slot_index", -1),
            "fired_slot_index": getattr(track, "fired_slot_index", -1),
            "is_frozen": getattr(track, "is_frozen", False),
            "can_be_frozen": getattr(track, "can_be_frozen", False),
            "color_index": getattr(track, "color_index", None),
            "clip_slots": clip_slots,
            "devices": devices
        }
        return result

    def _create_midi_track(self, index):
        """Create a new MIDI track at the specified index"""
        # Create the track
        self._song.create_midi_track(index)

        # Get the new track
        new_track_index = len(self._song.tracks) - 1 if index == -1 else index
        new_track = self._song.tracks[new_track_index]

        result = {
            "index": new_track_index,
            "name": new_track.name
        }
        return result


    def _set_track_name(self, track_index, name):
        """Set the name of a track"""
        # Set the name
        track = self._get_track(track_index)
        track.name = name

        result = {
            "name": track.name
        }
        return result

    def _create_clip(self, track_index, clip_index, length):
        """Create a new MIDI clip in the specified track and clip slot"""
        track = self._get_track(track_index)

        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")

        clip_slot = track.clip_slots[clip_index]

        # Check if the clip slot already has a clip
        if clip_slot.has_clip:
            raise Exception("Clip slot already has a clip")

        # Create the clip
        clip_slot.create_clip(length)

        result = {
            "name": clip_slot.clip.name,
            "length": clip_slot.clip.length
        }
        return result

    def _create_audio_clip(self, track_index, clip_index, path):
        """Create an audio clip in the specified audio track clip slot by importing a file.

        Requires Ableton Live 12.0.5 or newer (the underlying
        ClipSlot.create_audio_clip Live API was introduced in 12.0.5 - it is
        not available in earlier 12.0.x releases).
        """
        if not path:
            raise ValueError("Audio file path is required")

        if not os.path.isabs(path):
            raise ValueError(f"Audio file path must be absolute (got: {path})")

        track = self._get_track(track_index)

        # Must be an audio track. Audio tracks expose audio input; MIDI
        # tracks don't. Reject MIDI / return tracks up front so the caller
        # gets a clear error instead of a Live API exception.
        if getattr(track, "has_midi_input", False) or not getattr(track, "has_audio_input", True):
            raise ValueError(f"Track {track_index} is not an audio track")

        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")

        clip_slot = track.clip_slots[clip_index]

        if clip_slot.has_clip:
            raise Exception("Clip slot already has a clip")

        if not hasattr(clip_slot, "create_audio_clip"):
            raise Exception(
                "ClipSlot.create_audio_clip is unavailable in this Ableton Live "
                "version. Requires Live 12.0.5 or newer."
            )

        clip_slot.create_audio_clip(path)

        result = {
            "name": clip_slot.clip.name,
            "length": clip_slot.clip.length,
            "is_audio_clip": clip_slot.clip.is_audio_clip
        }
        return result

    def _add_notes_to_clip(self, track_index, clip_index, notes):
        """Add MIDI notes to a clip"""
        clip = self._get_clip(track_index, clip_index)
        if not clip.is_midi_clip:
            raise Exception("Clip is not a MIDI clip")

        # REPLACE the clip's note content (see _replace_all_notes)
        self._replace_all_notes(clip, notes)

        result = {
            "note_count": len(notes)
        }
        return result

    def _set_clip_name(self, track_index, clip_index, name):
        """Set the name of a clip"""
        clip = self._get_clip(track_index, clip_index)
        clip.name = name

        result = {
            "name": clip.name
        }
        return result

    def _set_tempo(self, tempo):
        """Set the tempo of the session"""
        self._song.tempo = tempo

        result = {
            "tempo": self._song.tempo
        }
        return result

    def _fire_clip(self, track_index, clip_index):
        """Fire a clip"""
        track = self._get_track(track_index)

        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")

        clip_slot = track.clip_slots[clip_index]

        if not clip_slot.has_clip:
            raise Exception("No clip in slot")

        clip_slot.fire()

        result = {
            "fired": True
        }
        return result

    def _stop_clip(self, track_index, clip_index):
        """Stop a clip"""
        track = self._get_track(track_index)

        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")

        clip_slot = track.clip_slots[clip_index]

        clip_slot.stop()

        result = {
            "stopped": True
        }
        return result


    def _start_playback(self):
        """Start playing the session"""
        self._song.start_playing()

        requested = {"playing": True}
        return self._later_readback(lambda: {"playing": bool(self._song.is_playing)},
                                    lambda state: state["playing"], requested)

    def _stop_playback(self):
        """Stop playing the session"""
        self._song.stop_playing()

        return self._later_readback(lambda: {"playing": bool(self._song.is_playing)},
                                    lambda state: not state["playing"], {"playing": False})

    # ── Arrangement view implementations ──────────────────────────────────────

    def _switch_to_arrangement_view(self):
        """Switch Ableton's main window to the Arrangement view"""
        self.application().view.show_view("Arranger")
        return {"view": "Arranger"}

    def _set_current_song_time(self, time_val):
        target = float(time_val)
        if not math.isfinite(target) or target < 0:
            raise ValueError("time must be finite and nonnegative")
        self._song.current_song_time = target
        assigned = time.monotonic()
        def matches(state):
            if not self._song.is_playing:
                return abs(state["current_song_time"] - target) <= 0.001
            elapsed_beats = (time.monotonic() - assigned) * float(self._song.tempo) / 60 if self._song.is_playing else 0
            return target - 0.001 <= state["current_song_time"] <= target + elapsed_beats + 0.1
        return self._later_readback(lambda: {"current_song_time": float(self._song.current_song_time)},
                                    matches, {"current_song_time": target})

    def _get_arrangement_clips(self, track_index):
        """Return all clips placed in the Arrangement timeline for a track.

        Each clip dict contains:
          name, start_time, end_time, length, color,
          is_midi_clip, is_audio_clip, is_playing
        """
        track = self._get_track(track_index)
        clips = []

        # track.arrangement_clips is available in Live 11 / 12
        for clip in track.arrangement_clips:
            clips.append({
                "name": clip.name,
                "start_time": clip.start_time,
                "end_time": clip.end_time,
                "length": clip.length,
                "color": clip.color,
                "is_midi_clip": clip.is_midi_clip,
                "is_audio_clip": clip.is_audio_clip,
                "is_playing": clip.is_playing,
                "file_path": getattr(clip, "file_path", None) if clip.is_audio_clip else None
            })

        return {
            "track_index": track_index,
            "track_name": track.name,
            "clip_count": len(clips),
            "clips": clips
        }

    def _duplicate_session_clip_to_arrangement(self, track_index, clip_index, destination_time):
        """Copy a Session-view clip into the Arrangement timeline.

        Uses the real Live API:
          track.duplicate_clip_to_arrangement(clip, destination_time)

        Available in Live 11 / 12.  destination_time is in beats from the
        start of the arrangement.
        """
        track = self._get_track(track_index)

        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip slot index out of range")

        clip_slot = track.clip_slots[clip_index]

        if not clip_slot.has_clip:
            raise Exception(
                "No clip in slot " + str(clip_index) +
                " on track " + str(track_index)
            )

        clip = clip_slot.clip

        # Duplicate to arrangement at the requested beat position
        track.duplicate_clip_to_arrangement(clip, float(destination_time))

        return {
            "success": True,
            "track_index": track_index,
            "track_name": track.name,
            "clip_name": clip.name,
            "destination_time": destination_time
        }

    # ── Browser implementations ───────────────────────────────────────────────

    def _load_browser_item(self, track_index, item_uri):
        """Load a browser item onto a regular track by its URI"""
        track = self._get_track(track_index)
        item = self._load_uri_onto_track(track, item_uri)
        return {"loaded": True, "item_name": item.name,
                "track_name": track.name, "uri": item_uri}

    # Substring markers that point a URI at a likely root. If no marker
    # matches we fall back to the default order, so this is purely an
    # optimisation - never a correctness change.
    _URI_ROOT_HINTS = (
        ('plugins',       ('vst:', 'vst3:', 'au:', 'query:plugins', 'plugin#')),
        ('max_for_live',  ('max for live', 'maxforlive', 'm4l', 'query:max')),
        ('user_library',  ('user library', 'userlibrary', 'query:user library', 'query:user-library')),
        ('packs',         ('query:packs', '/packs/')),
        ('samples',       ('query:samples', 'sample:', '/samples/')),
        ('drums',         ('query:drums', '/drums/')),
        ('instruments',   ('query:instruments', '/instruments/')),
        ('sounds',        ('query:sounds', '/sounds/')),
        ('audio_effects', ('query:audio effects', 'audioeffects', '/audio_effects/')),
        ('midi_effects',  ('query:midi effects', 'midieffects', '/midi_effects/')),
    )

    def _order_roots_by_uri(self, roots, uri):
        """Reorder ``roots`` so the URI's likely root is walked first."""
        if not isinstance(uri, (bytes, str)) or not uri:
            return roots
        lowered = uri.lower()
        for attr, markers in self._URI_ROOT_HINTS:
            if any(m in lowered for m in markers):
                head = [(a, r) for (a, r) in roots if a == attr]
                tail = [(a, r) for (a, r) in roots if a != attr]
                return head + tail
        return roots

    def _find_browser_item_by_uri(self, browser_or_item, uri, max_depth=10, current_depth=0):
        """Reuse a recent live handle or perform a bounded, synchronous lookup."""
        if not isinstance(uri, str) or not uri:
            raise ValueError("A nonempty browser item URI is required")
        cache = getattr(self, "_uri_cache", {})
        cached = cache.pop(uri, None)
        if cached:
            seen, item = cached
            try:
                if time.monotonic() - seen < self._BROWSER_CACHE_SECONDS and item.uri == uri:
                    cache[uri] = cached
                    return item
            except (AttributeError, RuntimeError):
                pass  # Live invalidates handles when browser contents change.
        return self._walk_browser_for_uri(browser_or_item, uri, max_depth, current_depth)

    def _walk_browser_for_uri(self, browser_or_item, uri, max_depth, current_depth):
        """Budgets bound our iteration; a single Live property access can still block."""
        reasons = set()
        if hasattr(browser_or_item, "children"):
            roots = [browser_or_item]
        else:
            available, errors = self._browser_roots(browser_or_item)
            if errors:
                reasons.add("unreadable_root")
            roots = [root for _, root in self._order_roots_by_uri(list(available.items()), uri)]
        stack = [(iter(roots), current_depth)]
        deadline, visited = time.monotonic() + self._BROWSER_LOOKUP_SECONDS, 0
        while stack:
            iterator, depth = stack[-1]
            try:
                item = next(iterator)
            except StopIteration:
                stack.pop()
                continue
            except Exception:
                reasons.add("unreadable_item")
                stack.pop()
                continue
            visited += 1
            if visited > self._BROWSER_LOOKUP_NODES or time.monotonic() >= deadline:
                reasons.add("scan_budget")
                break
            try:
                if getattr(item, "uri", None) == uri:
                    self._cache_browser_item(item)
                    return item
                children = getattr(item, "children", ())
                if children:
                    if depth >= max_depth:
                        reasons.add("depth_limit")
                    else:
                        stack.append((iter(children), depth + 1))
            except Exception:
                reasons.add("unreadable_item")
        if reasons:
            raise CommandFailure("Browser URI lookup incomplete; use search_browser to obtain a fresh URI", {
                "phase": "browser_lookup", "partial": True, "limit_reasons": sorted(reasons),
                "may_have_applied": False})
        return None

    # Helper methods

    # ── Scenes, deletes, mixer, and device parameters ─────────────────

    def _get_track(self, track_index):
        if track_index < 0 or track_index >= len(self._song.tracks):
            raise IndexError("Track index out of range")
        return self._song.tracks[track_index]

    def _get_scene(self, scene_index):
        if scene_index < 0 or scene_index >= len(self._song.scenes):
            raise IndexError("Scene index out of range")
        return self._song.scenes[scene_index]

    def _create_scene(self, index):
        self._song.create_scene(index)
        return {"scene_count": len(self._song.scenes)}

    def _delete_clip(self, track_index, clip_index):
        track = self._get_track(track_index)
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        slot = track.clip_slots[clip_index]
        if not slot.has_clip:
            raise Exception("No clip in slot")
        slot.delete_clip()
        return {"deleted": True}

    def _delete_arrangement_clip(self, track_index, arrangement_clip_index):
        track = self._get_track(track_index)
        clips = list(track.arrangement_clips)
        if arrangement_clip_index < 0 or arrangement_clip_index >= len(clips):
            raise IndexError("Arrangement clip index out of range")
        clip = clips[arrangement_clip_index]
        info = {"name": clip.name, "start_time": clip.start_time,
                "end_time": clip.end_time, "deleted": True}
        track.delete_clip(clip)
        return info

    def _delete_track(self, track_index):
        self._get_track(track_index)
        self._song.delete_track(track_index)
        return {"deleted": True, "track_count": len(self._song.tracks)}

    def _set_track_volume(self, track_index, volume):
        p = self._get_track(track_index).mixer_device.volume
        p.value = max(p.min, min(p.max, float(volume)))
        return {"volume": p.value}

    def _set_track_pan(self, track_index, pan):
        p = self._get_track(track_index).mixer_device.panning
        p.value = max(p.min, min(p.max, float(pan)))
        return {"panning": p.value}

    def _set_track_mute(self, track_index, mute):
        track = self._get_track(track_index)
        track.mute = bool(mute)
        return {"mute": track.mute}

    def _set_track_solo(self, track_index, solo):
        track = self._get_track(track_index)
        track.solo = bool(solo)
        return {"solo": track.solo}

    def _set_master_volume(self, volume):
        p = self._song.master_track.mixer_device.volume
        p.value = max(p.min, min(p.max, float(volume)))
        return {"volume": p.value}

    def _set_send(self, track_index, send_index, value):
        sends = self._get_track(track_index).mixer_device.sends
        if send_index < 0 or send_index >= len(sends):
            raise IndexError("Send index out of range")
        p = sends[send_index]
        p.value = max(p.min, min(p.max, float(value)))
        return {"send": send_index, "value": p.value}

    def _resolve_track(self, track_index, track_type="track"):
        """Resolve a track by type: 'track' (regular), 'return', or 'master'."""
        if track_type == "master":
            return self._song.master_track
        if track_type == "return":
            returns = self._song.return_tracks
            if track_index < 0 or track_index >= len(returns):
                raise IndexError("Return track index out of range")
            return returns[track_index]
        return self._get_track(track_index)

    def _get_device(self, track_index, device_index, track_type="track"):
        track = self._resolve_track(track_index, track_type)
        if device_index < 0 or device_index >= len(track.devices):
            raise IndexError("Device index out of range")
        return track.devices[device_index]

    def _set_device_parameter(self, track_index, device_index, parameter, value, track_type="track"):
        device = self._get_device(track_index, device_index, track_type)
        param = self._resolve_parameter(device, parameter)
        if not param.is_enabled:
            raise Exception("Parameter is disabled: " + param.name)
        native, warning = _parameter_value(param, value)
        param.value = native
        result = {"device": device.name, "parameter": param.name, "value": param.value,
                  "native": param.value, "requested": value, "warning": warning}
        try:
            result["display"] = param.str_for_value(param.value)
        except Exception:
            pass
        return result

    def _get_device_parameters(self, track_index, device_index, track_type="track"):
        device = self._get_device(track_index, device_index, track_type)
        params = []
        for i, p in enumerate(device.parameters):
            entry = {"index": i, "name": p.name, "value": p.value,
                     "min": p.min, "max": p.max,
                     "is_quantized": p.is_quantized, "is_enabled": p.is_enabled,
                     "automation_state": int(getattr(p, "automation_state", 0))}
            if p.is_quantized:
                try:
                    entry["value_items"] = [str(v) for v in p.value_items]
                except Exception:
                    pass
            try:
                entry["display"] = p.str_for_value(p.value)
            except Exception:
                pass
            params.append(entry)
        return {"device": device.name, "parameter_count": len(params),
                "parameters": params}

    def _get_clip_notes(self, track_index, clip_index):
        clip = self._get_clip(track_index, clip_index)
        if not clip.is_midi_clip:
            raise Exception("Not a MIDI clip")
        notes = self._read_all_notes(clip, 32001)
        if len(notes) > 32000:
            raise ValueError("Clip exceeds the 32000-note full-read limit; use bounded session analysis or a smaller clip")
        return {"clip_name": clip.name, "length": clip.length,
                "note_count": len(notes), "notes": notes}

    # ── Grooves, audio clips, routing, automation, and clip ops ───────

    def _get_clip(self, track_index, clip_index):
        track = self._get_track(track_index)
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        slot = track.clip_slots[clip_index]
        if not slot.has_clip:
            raise Exception("No clip in slot")
        return slot.clip

    # -- grooves --
    def _get_grooves(self):
        pool = getattr(self._song, "groove_pool", None)
        grooves = list(pool.grooves) if pool else []
        return {"groove_amount": getattr(self._song, "groove_amount", None),
                "groove_count": len(grooves),
                "grooves": [{"index": i, "name": g.name} for i, g in enumerate(grooves)]}

    def _set_clip_groove(self, track_index, clip_index, groove_index):
        clip = self._get_clip(track_index, clip_index)
        if groove_index is None:
            try:
                clip.groove = None
                return {"groove": None}
            except Exception:
                # Some Live builds reject assigning None to clip.groove.
                return {"groove": clip.groove.name if clip.groove else None,
                        "note": "clearing not supported in this Live build; assign a groove index to change"}
        pool = self._song.groove_pool
        grooves = list(pool.grooves)
        if groove_index < 0 or groove_index >= len(grooves):
            raise IndexError(f"Groove index out of range (pool has {len(grooves)})")
        clip.groove = grooves[groove_index]
        return {"groove": grooves[groove_index].name}

    # -- quantize --
    # Grid -> member basenames (Live's enum names are misspelled: eight, sixtenth...).
    # clip.quantize() accepts only a subset of Quantization; finer grids come from
    # RecordingQuantization. We try both enums (and raw ints) and use whatever works.
    _Q_BASE = {
        "quarter": ["quarter"],
        "eighth": ["eight", "eighth"],
        "eighth_triplet": ["eight_triplet"],
        "sixteenth": ["sixtenth", "sixteenth"],
        "sixteenth_triplet": ["sixtenth_triplet"],
        "thirtysecond": ["thirtytwoth", "thirtysecond"],
    }

    def _quantize_clip(self, track_index, clip_index, grid, amount):
        clip = self._get_clip(track_index, clip_index)
        import Live
        amt = max(0.0, min(1.0, float(amount)))
        base = self._Q_BASE.get(grid)
        if base is None:
            raise Exception("Unknown grid '" + str(grid) + "'. Valid: " + ", ".join(sorted(self._Q_BASE)))
        candidates = []
        for enum_name, prefix in (("RecordingQuantization", "rec_q_"), ("Quantization", "q_")):
            enum = getattr(Live.Song, enum_name, None)
            if enum is None:
                continue
            for b in base:
                m = prefix + b
                if hasattr(enum, m):
                    candidates.append(getattr(enum, m))
        last = None
        for val in candidates:
            try:
                clip.quantize(val, amt)
                return {"quantized": True, "grid": grid, "amount": amt}
            except Exception as e:
                last = str(e)
        raise Exception(f"quantize failed for grid '{grid}' ({len(candidates)} candidates): {last}")

    # -- audio clip properties --
    def _set_clip_audio(self, track_index, clip_index, params):
        clip = self._get_clip(track_index, clip_index)
        if not clip.is_audio_clip:
            raise Exception("Not an audio clip")
        applied = {}
        if "gain" in params:
            clip.gain = max(0.0, min(1.0, float(params["gain"])))
            applied["gain"] = clip.gain
        if "pitch_coarse" in params:
            clip.pitch_coarse = int(params["pitch_coarse"])
            applied["pitch_coarse"] = clip.pitch_coarse
        if "pitch_fine" in params:
            clip.pitch_fine = float(params["pitch_fine"])
            applied["pitch_fine"] = clip.pitch_fine
        if "warping" in params:
            clip.warping = bool(params["warping"])
            applied["warping"] = clip.warping
        if "warp_mode" in params:
            clip.warp_mode = int(params["warp_mode"])
            applied["warp_mode"] = clip.warp_mode
        return applied

    # -- track creation --
    def _create_return_track(self):
        self._song.create_return_track()
        return {"return_track_count": len(self._song.return_tracks)}

    def _create_audio_track(self, index):
        self._song.create_audio_track(index)
        new_index = len(self._song.tracks) - 1 if index == -1 else index
        return {"index": new_index, "track_count": len(self._song.tracks),
                "id": self._snapshot_track_ids(list(self._song.tracks))[new_index]}

    def _get_return_tracks(self):
        return {"return_tracks": [{"index": i, "name": t.name}
                                  for i, t in enumerate(self._song.return_tracks)]}

    # -- automation (clip envelopes) --
    def _resolve_parameter(self, device, parameter):
        if isinstance(parameter, int):
            if parameter < 0 or parameter >= len(device.parameters):
                raise IndexError("Parameter index out of range")
            return device.parameters[parameter]
        for p in device.parameters:
            if p.name == parameter:
                return p
        raise Exception("Parameter not found: " + str(parameter))

    def _write_automation(self, track_index, clip_index, device_index, parameter, points):
        points = self._prepare_events(points, "points")
        clip = self._get_clip(track_index, clip_index)
        device = self._get_device(track_index, device_index)
        param = self._resolve_parameter(device, parameter)
        if any(not param.min <= pt["value"] <= param.max for pt in points):
            raise ValueError("Automation values must be within the parameter's native range")
        # Live only creates a clip envelope for the currently-viewed detail clip.
        try:
            self._song.view.detail_clip = clip
        except Exception:
            pass
        clip.clear_envelope(param)
        env = clip.automation_envelope(param)
        if env is None and hasattr(clip, "create_automation_envelope"):
            env = clip.create_automation_envelope(param)
        if env is None:
            raise Exception(
                "Automation envelope unavailable for '{}' on '{}' (class={}). "
                "This Live build may not support clip automation for this parameter.".format(
                    param.name, device.name, getattr(device, "class_name", "?")))
        for pt in points:
            env.insert_step(float(pt["time"]), 0.0, float(pt["value"]))
        return {"parameter": param.name, "point_count": len(points),
                "device": device.name}

    def _clear_automation(self, track_index, clip_index, device_index, parameter):
        clip = self._get_clip(track_index, clip_index)
        device = self._get_device(track_index, device_index)
        param = self._resolve_parameter(device, parameter)
        clip.clear_envelope(param)
        return {"cleared": True, "parameter": param.name}

    # -- locators --
    def _create_locator(self, name, target=None):
        # Cue creation reads the playhead. A moving playhead cannot be pinned to
        # an exact requested beat without changing playback, so fail before edits.
        if self._song.is_playing:
            raise ValueError("Stop playback before placing an exact locator")
        target = float(self._song.current_song_time if target is None else target)
        seek = self._set_current_song_time(target)
        def start(finish):
            def after_seek(result=None, error=None):
                if error or not result["confirmed"]:
                    finish(error=error or ValueError("Seek not confirmed; no locator created"))
                    return
                try:
                    if self._song.is_playing or abs(float(self._song.current_song_time) - target) > 0.001:
                        raise ValueError("Playhead changed before locator creation")
                    existing = next((cue for cue in self._song.cue_points if abs(cue.time - target) < 0.001), None)
                    created = existing is None
                    if created:
                        self._song.set_or_delete_cue()
                    def read():
                        cue = next((cue for cue in self._song.cue_points if abs(cue.time - target) < 0.001), None)
                        if cue is not None and name:
                            cue.name = name
                        return {"time": target, "name": cue.name if cue else None,
                                "created": created if cue else None, "found": cue is not None}
                    self._later_readback(read, lambda state: state["found"], {"time": target, "name": name}).start(finish)
                except Exception as exc:
                    finish(error=exc)
            seek.start(after_seek)
        return _Deferred(start)

    # -- scenes --
    def _fire_scene(self, scene_index):
        self._get_scene(scene_index).fire()
        return {"fired": scene_index}

    def _set_scene_name(self, scene_index, name):
        scene = self._get_scene(scene_index)
        scene.name = name
        return {"name": scene.name}

    # -- transport / meter / loop --
    def _set_time_signature(self, numerator, denominator):
        self._song.signature_numerator = int(numerator)
        self._song.signature_denominator = int(denominator)
        return {"numerator": self._song.signature_numerator,
                "denominator": self._song.signature_denominator}

    def _set_loop(self, start, length, enabled):
        self._song.loop_start = float(start)
        self._song.loop_length = float(length)
        self._song.loop = bool(enabled)
        return {"loop_start": self._song.loop_start,
                "loop_length": self._song.loop_length, "loop": self._song.loop}

    def _set_clip_loop(self, track_index, clip_index, params):
        clip = self._get_clip(track_index, clip_index)
        applied = {}
        if "looping" in params:
            clip.looping = bool(params["looping"])
            applied["looping"] = clip.looping
        if "start" in params:
            clip.loop_start = float(params["start"])
            applied["loop_start"] = clip.loop_start
        if "end" in params:
            clip.loop_end = float(params["end"])
            applied["loop_end"] = clip.loop_end
        if "start_marker" in params:
            clip.start_marker = float(params["start_marker"])
            applied["start_marker"] = clip.start_marker
        if "end_marker" in params:
            clip.end_marker = float(params["end_marker"])
            applied["end_marker"] = clip.end_marker
        return applied

    # -- device enable / master+return device loading --
    def _set_device_enabled(self, track_index, device_index, enabled):
        device = self._get_device(track_index, device_index)
        device.parameters[0].value = 1.0 if enabled else 0.0
        return {"device": device.name, "enabled": bool(enabled)}

    def _load_uri_onto_track(self, track, item_uri):
        """Select a track and load a browser item onto it by URI (shared helper)."""
        app = self.application()
        item = self._find_browser_item_by_uri(app.browser, item_uri)
        if not item:
            raise ValueError(f"Browser item with URI '{item_uri}' not found")
        if not item.is_loadable:
            raise ValueError("Browser item is not loadable: " + item_uri)
        self._song.view.selected_track = track
        try:
            app.browser.load_item(item)
        except Exception:
            self._uri_cache.pop(item_uri, None)
            raise
        return item

    def _load_device_to_return(self, return_index, item_uri):
        item = self._load_uri_onto_track(self._resolve_track(return_index, "return"), item_uri)
        return {"loaded": True, "item_name": item.name, "return": return_index}

    def _load_device_to_master(self, item_uri):
        item = self._load_uri_onto_track(self._song.master_track, item_uri)
        return {"loaded": True, "item_name": item.name, "track": "master"}

    # -- note-level editing --
    def _edit_notes(self, track_index, clip_index, add, remove):
        add = self._prepare_events(add, "add")
        remove = self._prepare_events(remove, "remove")
        clip = self._get_clip(track_index, clip_index)
        if not clip.is_midi_clip:
            raise Exception("Not a MIDI clip")
        if hasattr(clip, "remove_notes_extended") and hasattr(clip, "add_new_notes"):
            # Targeted O(adds+removes) edit - no full rewrite.
            specs = tuple(self._make_note_spec(n) for n in add)
            removed = 0
            for rm in remove:
                pitch, start = int(rm["pitch"]), float(rm["start_time"])
                before = len(clip.get_notes_extended(pitch, 1, start - 1e-3, 2e-3))
                clip.remove_notes_extended(pitch, 1, start - 1e-3, 2e-3)
                removed += before
            if add:
                clip.add_new_notes(specs)
            total = len(clip.get_all_notes_extended())
            return {"note_count": total, "added": len(add), "removed": removed}
        # legacy fallback: read-filter-rewrite
        existing = self._read_all_notes(clip)

        def matches(note, rm):
            return (abs(note["start_time"] - rm["start_time"]) < 1e-3
                    and note["pitch"] == rm["pitch"])

        kept = [n for n in existing if not any(matches(n, rm) for rm in remove)]
        removed = len(existing) - len(kept)
        kept.extend(add)
        self._replace_all_notes(clip, kept)
        return {"note_count": len(kept), "added": len(add), "removed": removed}

    # -- undo / redo --
    def _undo(self):
        self._song.undo()
        return {"undone": True}

    def _redo(self):
        self._song.redo()
        return {"redone": True}

    # -- colors --
    @staticmethod
    def _validate_color(color_index):
        ci = int(color_index)
        if not 0 <= ci <= 69:
            raise ValueError("color_index must be 0-69 (Live's 70-color palette)")
        return ci

    def _set_track_color(self, track_index, color_index):
        ci = self._validate_color(color_index)
        self._get_track(track_index).color_index = ci
        return {"color_index": ci}

    def _set_clip_color(self, track_index, clip_index, color_index):
        ci = self._validate_color(color_index)
        self._get_clip(track_index, clip_index).color_index = ci
        return {"color_index": ci}

    def _duplicate_track(self, track_index):
        self._get_track(track_index)
        self._song.duplicate_track(track_index)
        return {"track_count": len(self._song.tracks)}

    # MCP tool names that differ from wire command names - batch accepts both.
    _BATCH_ALIASES = {
        "clip_operation": "clip_op",
        "duplicate_to_arrangement": "duplicate_session_clip_to_arrangement",
        "set_arrangement_time": "set_current_song_time",
        "load_instrument_or_effect": "load_browser_item",
        "save_set": "try_save_project",
        "batch_commands": "batch",
    }

    # Operations that cannot truthfully fit a synchronous undo group.
    _BATCH_EXCLUDED = {"batch", "create_locator", "try_save_project", "undo", "redo",
                       "start_playback", "stop_playback", "set_current_song_time",
                       "continue_playing", "jump_by", "jump_to_cue", "jump_to_locator",
                       "search_browser", "get_session_notes"}

    @classmethod
    def _validate_batch_values(cls, params):
        """Static validation only: object existence is checked at execution time."""
        numeric = {"time", "start", "end", "length", "destination_time", "region_start",
                   "region_length", "tempo", "volume", "pan", "amount", "beats",
                   "loop_start", "loop_end", "start_marker", "end_marker", "gain",
                   "pitch_fine", "pitch_coarse", "record_length"}
        toggles = {"enabled", "arm", "mute", "solo", "folded", "looping", "warping"}
        strings = {"name", "item_uri", "path", "field", "display_name", "scale_name", "op", "grid", "expected_track_id"}
        for key, value in params.items():
            if value is None:
                if key in ("groove_index", "root_note", "scale_name", "record_length"):
                    continue
                raise ValueError(key + " cannot be null")
            if key.endswith("_index") or key in ("index", "src_track", "src_scene", "dst_track", "dst_scene"):
                minimum = -1 if key == "index" else 0
                if type(value) is not int or value < minimum:
                    raise ValueError(key + " must be an integer >= " + str(minimum))
            if key in toggles and type(value) is not bool:
                raise ValueError(key + " must be a boolean")
            if key in strings and not isinstance(value, str):
                raise ValueError(key + " must be a string")
            if key == "parameter" and not (isinstance(value, str) or type(value) is int and value >= 0):
                raise ValueError("parameter must be a name or nonnegative integer")
            if key in numeric or (key == "value" and not isinstance(value, str)):
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(key + " must be a finite number")
            if key in ("length", "region_length", "record_length") and value <= 0:
                raise ValueError(key + " must be positive")
            if key in ("pitch", "transposition_amount", "warp_mode", "mode") and type(value) is not int:
                raise ValueError(key + " must be an integer")
            if key in ("time", "start", "destination_time", "region_start", "loop_start", "loop_end", "start_marker", "end_marker") and value < 0:
                raise ValueError(key + " cannot be negative")
            ranges = {"tempo": (20, 999), "volume": (0, 1), "pan": (-1, 1),
                      "amount": (0, 1), "color_index": (0, 69), "root_note": (0, 11),
                      "state": (0, 2), "assign": (0, 2), "note": (0, 127),
                      "numerator": (1, 99), "denominator": (1, 16)}
            if key in ranges:
                low, high = ranges[key]
                if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(key + " must be in " + str((low, high)))
            if key in ("state", "assign", "note", "numerator", "denominator", "color_index", "root_note") and type(value) is not int:
                raise ValueError(key + " must be an integer")
        if "denominator" in params and params["denominator"] not in (1, 2, 4, 8, 16):
            raise ValueError("denominator must be 1, 2, 4, 8 or 16")
        for start, end in (("start", "end"), ("start_marker", "end_marker")):
            if start in params and end in params and params[start] >= params[end]:
                raise ValueError(end + " must be after " + start)
        for key in ("notes", "add", "remove", "points"):
            if key in params:
                cls._prepare_events(params[key], key)

    def _normalize_batch_command(self, command):
        if not isinstance(command, dict) or not isinstance(command.get("type"), str):
            raise ValueError("Each command must be an object with a string type")
        original = command["type"]
        ctype = self._BATCH_ALIASES.get(original, original)
        if ctype not in self._ALL_COMMANDS:
            raise ValueError("Unknown or composite batch command: " + original)
        if ctype in self._BATCH_EXCLUDED:
            raise ValueError(original + " is not supported in a batch; use the standalone tool")
        params = command.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Command params must be an object")
        params = dict(params)
        if ctype == "load_browser_item" and "uri" in params:
            uri = params.pop("uri")
            if "item_uri" in params and params["item_uri"] != uri:
                raise ValueError("Conflicting uri and item_uri")
            params["item_uri"] = uri
        if ctype == "clip_op":
            nested = params.pop("params", {})
            if not isinstance(nested, dict):
                raise ValueError("clip_op.params must be an object")
            nested = dict(nested)
            for key in ("region_start", "region_length", "destination_time", "pitch", "transposition_amount"):
                if key in params:
                    value = params.pop(key)
                    if key in nested and nested[key] != value:
                        raise ValueError("Conflicting clip region argument: " + key)
                    nested[key] = value
            if params.get("op") not in ("duplicate_loop", "crop", "duplicate_region"):
                raise ValueError("Unsupported clip operation")
            allowed = {"region_start", "region_length", "destination_time", "pitch", "transposition_amount"} if params["op"] == "duplicate_region" else set()
            if set(nested) - allowed:
                raise ValueError("Unsupported clip region argument(s): " + ", ".join(sorted(set(nested) - allowed)))
            if params["op"] == "duplicate_region":
                for key in ("region_start", "region_length", "destination_time"):
                    if key not in nested:
                        raise ValueError("Missing region argument: " + key)
            self._validate_batch_values(nested)
            params["params"] = nested
        self._validate_batch_values(params)
        # Bind exactly the same dispatch lambda as execution, but to a probe
        # whose methods have no Live access. Required-argument checks cannot drift.
        probe = _ProbeParams(params)
        self._ALL_COMMANDS[ctype](_BatchProbe(), probe)
        extra = {"expected_track_id"}
        if ctype == "set_clip_loop":
            extra.update(("start", "end", "looping", "start_marker", "end_marker"))
        if ctype == "set_clip_audio":
            extra.update(("gain", "pitch_coarse", "pitch_fine", "warping", "warp_mode"))
        unknown = set(params) - probe.consumed - extra
        if unknown:
            raise ValueError("Unsupported parameter(s): " + ", ".join(sorted(unknown)))
        if "value" in params and "parameter" not in params and type(params["value"]) not in (int, float):
            raise ValueError("value must be numeric for this command")
        if ctype == "rack_variation" and params.get("action") not in ("store", "recall", "randomize"):
            raise ValueError("Unsupported rack variation action")
        if ctype == "quantize_clip" and params.get("grid", "sixteenth") not in self._Q_BASE:
            raise ValueError("Unsupported quantize grid")
        if ctype in ("set_track_routing", "set_device_routing") and params.get("field") not in self._ROUTING_FIELDS:
            raise ValueError("Unsupported routing field")
        if ctype == "set_device_routing" and params.get("field") not in ("input_routing_type", "input_routing_channel"):
            raise ValueError("Device routing supports input fields only")
        if ctype == "set_simpler_playback_mode" and params.get("mode") not in (0, 1, 2):
            raise ValueError("Simpler mode must be 0, 1 or 2")
        return {"type": ctype, "original_type": original, "params": params}

    def _batch(self, commands):
        """Preflight all commands, then stop on the first runtime failure. No rollback."""
        if not isinstance(commands, list) or not 1 <= len(commands) <= 128:
            raise ValueError("Batch must contain 1 to 128 commands")
        normalized = []
        for index, command in enumerate(commands):
            try:
                normalized.append(self._normalize_batch_command(command))
            except Exception as exc:
                raise CommandFailure(f"Batch preflight failed at step {index + 1}: {exc}",
                                     {"phase": "preflight", "failed_index": index,
                                      "failed_step": index + 1, "completed_count": 0,
                                      "completed_results": [], "command": command,
                                      "may_have_applied": False})
        results = []
        self._song.begin_undo_step()
        try:
            for index, command in enumerate(normalized):
                try:
                    params = self._guard_target(command["params"])
                    result = self._ALL_COMMANDS[command["type"]](self, params)
                    results.append({"type": command["type"], "original_type": command["original_type"], "result": result})
                except Exception as exc:
                    details = dict(getattr(exc, "details", {}))
                    details.update({"phase": "execution", "failed_index": index,
                                    "failed_step": index + 1, "command": command,
                                    "completed_count": len(results), "completed_results": results,
                                    "may_have_applied": bool(results) or details.get("may_have_applied", True)})
                    raise CommandFailure(f"Batch failed at step {index + 1} of {len(commands)}: {exc}. Completed edits were not rolled back.",
                                         details)
        finally:
            self._song.end_undo_step()
        return {"executed": len(results), "results": results}

    # -- browser search by display name --
    _SCAN_TICK_SECONDS = 0.005
    _SCAN_TOTAL_SECONDS = 6.0
    _SCAN_STEPS_PER_TICK = 256
    _BROWSER_NODE_LIMIT = 50000

    def _search_browser(self, query, category, max_results):
        """Fair traversal across roots, continued on later Live ticks."""
        if type(max_results) is not int or max_results < 0:
            raise ValueError("max_results must be a nonnegative integer")
        if not isinstance(query, str):
            raise ValueError("query must be a string (empty lists loadable items)")
        requested_limit = max_results
        max_results = min(max_results, 1000)
        available, root_errors = self._browser_roots(category=category)
        roots = list(available.values())
        query_lower = query.lower()
        # Each root gets one traversal step at a time; a large Sounds tree
        # cannot consume every tick before Audio Effects gets a chance.
        active = deque([(iter((root,)), -1)] for root in roots)
        matches, reasons = [], {"unreadable_root"} if root_errors else set()
        visited = [0]
        def start(finish):
            deadline = time.monotonic() + self._SCAN_TOTAL_SECONDS
            def report():
                return {"query": query, "match_count": len(matches), "matches": matches,
                        "visited": visited[0], "partial": bool(reasons),
                        "limit_reasons": sorted(reasons), "requested_max_results": requested_limit,
                        "max_results": max_results}
            def tick():
                tick_end = time.monotonic() + self._SCAN_TICK_SECONDS
                steps = 0
                try:
                    while active and steps < self._SCAN_STEPS_PER_TICK and time.monotonic() < tick_end:
                        if len(matches) >= max_results:
                            reasons.add("result_limit")
                            break
                        if visited[0] >= self._BROWSER_NODE_LIMIT or time.monotonic() >= deadline:
                            reasons.add("scan_budget")
                            break
                        stack = active.popleft()
                        iterator, depth = stack[-1]
                        steps += 1
                        try:
                            child = next(iterator)
                        except StopIteration:
                            stack.pop()
                        except Exception:
                            stack.pop()
                            reasons.add("unreadable_item")
                        else:
                            if depth >= 0:
                                visited[0] += 1
                            try:
                                if depth >= 0 and query_lower in child.name.lower() and child.is_loadable:
                                    matches.append({"name": child.name, "uri": self._cache_browser_item(child),
                                                    "is_device": child.is_device})
                                if depth < 0 or child.is_folder:
                                    if depth >= 8:
                                        reasons.add("depth_limit")
                                    else:
                                        stack.append((iter(child.children), depth + 1))
                            except Exception:
                                reasons.add("unreadable_item")
                        if stack:
                            active.append(stack)
                    if active and time.monotonic() >= deadline:
                        reasons.add("scan_budget")
                    if not active or "scan_budget" in reasons or "result_limit" in reasons:
                        finish(result=report())
                    else:
                        self.schedule_message(1, tick)
                except Exception as exc:
                    finish(error=exc)
            self.schedule_message(1, tick)
        return _Deferred(start)

    # -- recording --
    def _set_track_arm(self, track_index, arm):
        track = self._get_track(track_index)
        if not track.can_be_armed:
            raise Exception(f"Track {track_index} cannot be armed")
        track.arm = bool(arm)
        return {"arm": track.arm}

    def _capture_midi(self):
        self._song.capture_midi()
        return {"captured": True}

    def _set_song_scale(self, root_note, scale_name):
        applied = {}
        if root_note is not None:
            self._song.root_note = int(root_note)
            applied["root_note"] = self._song.root_note
        if scale_name is not None:
            self._song.scale_name = scale_name
            applied["scale_name"] = self._song.scale_name
        return applied

    # -- transport/global extras --
    def _set_metronome(self, enabled):
        self._song.metronome = bool(enabled)
        return {"metronome": self._song.metronome}

    def _stop_all_clips(self):
        self._song.stop_all_clips()
        return {"stopped": True}

    def _back_to_arranger(self):
        self._song.back_to_arranger = False
        return {"back_to_arranger": True}

    def _set_record_mode(self, enabled):
        self._song.record_mode = bool(enabled)
        return {"record_mode": self._song.record_mode}

    def _set_session_record(self, enabled):
        self._song.session_record = bool(enabled)
        return {"session_record": self._song.session_record}

    @staticmethod
    def _meter(track, attr):
        # Live RAISES (not AttributeError) on tracks with MIDI output, so a
        # getattr default won't catch it - return None for "no meter here".
        try:
            return float(getattr(track, attr))
        except Exception:
            return None

    def _get_track_meters(self, track_index):
        track = self._get_track(track_index)
        left = self._meter(track, "output_meter_left")
        if left is None:
            return {"track": track.name, "left": None, "right": None, "peak": None,
                    "note": "track has MIDI output - no audio meters"}
        return {"track": track.name, "left": left,
                "right": self._meter(track, "output_meter_right"),
                "peak": self._meter(track, "output_meter_level")}

    def _get_master_meters(self):
        m = self._song.master_track
        return {"left": self._meter(m, "output_meter_left"),
                "right": self._meter(m, "output_meter_right"),
                "peak": self._meter(m, "output_meter_level")}

    # ── note API helpers (Live 11+ extended API, legacy fallback) ────
    @staticmethod
    def _prepare_events(values, kind):
        """Validate the whole payload before touching Live; keep note defaults."""
        if not isinstance(values, list) or len(values) > 32000:
            raise ValueError(kind + " must be a list of at most 32000 items")
        prepared = []
        ranges = {"pitch": (0, 127), "velocity": (0, 127), "probability": (0, 1),
                  "velocity_deviation": (-127, 127), "release_velocity": (0, 127)}
        for item in values:
            if not isinstance(item, dict):
                raise ValueError(kind + " entries must be objects")
            if kind == "points":
                event = {key: item.get(key) for key in ("time", "value")}
            elif kind == "remove":
                event = {key: item.get(key) for key in ("pitch", "start_time")}
            else:
                event = dict(pitch=60, start_time=0.0, duration=0.25, velocity=100, mute=False)
                event.update({key: value for key, value in item.items()
                              if key in event or key in ranges})
            for key, value in event.items():
                if key == "mute":
                    if type(value) is not bool:
                        raise ValueError("mute must be a boolean")
                    continue
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(kind + "." + key + " must be a finite number")
                if key in ranges and not ranges[key][0] <= value <= ranges[key][1]:
                    raise ValueError(key + " must be in " + str(ranges[key]))
                if key == "pitch" and int(value) != value:
                    raise ValueError("pitch must be an integer")
                if (key == "duration" and value <= 0) or (key == "time" and value < 0):
                    raise ValueError("Invalid " + key)
            prepared.append(event)
        return prepared

    @staticmethod
    def _make_note_spec(n):
        import Live
        # _prepare_events supplies validated defaults and optional extended fields.
        kwargs = {key: float(value) for key, value in n.items() if key not in ("pitch", "mute")}
        kwargs.update(pitch=int(n["pitch"]), mute=n["mute"])
        try:
            return Live.Clip.MidiNoteSpecification(**kwargs)
        except TypeError:
            # Older build without the extended kwargs - drop them.
            for field in ("probability", "velocity_deviation", "release_velocity"):
                kwargs.pop(field, None)
            return Live.Clip.MidiNoteSpecification(**kwargs)

    def _replace_all_notes(self, clip, note_dicts):
        """Prepare all notes before clearing; legacy set_notes only adds."""
        note_dicts = self._prepare_events(note_dicts, "notes")
        if hasattr(clip, "remove_notes_extended") and hasattr(clip, "add_new_notes"):
            specs = tuple(self._make_note_spec(n) for n in note_dicts)
            clip.remove_notes_extended(0, 128, -(2 ** 14), 2 ** 15)
            clip.add_new_notes(specs)
        else:
            notes = tuple((int(n["pitch"]), float(n["start_time"]), float(n["duration"]),
                           int(n["velocity"]), n["mute"]) for n in note_dicts)
            clip.remove_notes(-(2 ** 14), 0, 2 ** 15, 128)
            clip.set_notes(notes)

    def _read_all_notes(self, clip, max_notes=None):
        """Read notes via the extended API (incl. probability/deviation) when available."""
        if hasattr(clip, "get_all_notes_extended"):
            out = []
            for n in clip.get_all_notes_extended():
                if max_notes is not None and len(out) >= max_notes:
                    break
                out.append({"pitch": n.pitch, "start_time": n.start_time,
                            "duration": n.duration, "velocity": n.velocity,
                            "mute": n.mute,
                            "probability": getattr(n, "probability", 1.0),
                            "velocity_deviation": getattr(n, "velocity_deviation", 0.0)})
            return out
        raw = clip.get_notes(0.0, 0, clip.length, 128)
        if max_notes is not None:
            raw = raw[:max_notes]
        return [{"pitch": q[0], "start_time": q[1], "duration": q[2],
                 "velocity": q[3], "mute": q[4]} for q in raw]

    # ── additional commands ──
    def _continue_playing(self):
        self._song.continue_playing()
        return {"continued": True}

    def _set_clip_trigger_quantization(self, value):
        self._song.clip_trigger_quantization = int(value)
        return {"clip_trigger_quantization": int(self._song.clip_trigger_quantization)}

    def _delete_scene(self, scene_index):
        self._get_scene(scene_index)
        self._song.delete_scene(scene_index)
        return {"scene_count": len(self._song.scenes)}

    def _duplicate_scene(self, scene_index):
        self._get_scene(scene_index)
        self._song.duplicate_scene(scene_index)
        return {"scene_count": len(self._song.scenes)}

    def _delete_return_track(self, return_index):
        self._resolve_track(return_index, "return")
        self._song.delete_return_track(return_index)
        return {"return_track_count": len(self._song.return_tracks)}

    def _capture_and_insert_scene(self):
        self._song.capture_and_insert_scene()
        return {"scene_count": len(self._song.scenes)}

    def _get_locators(self):
        return {"locators": [{"index": i, "name": c.name, "time": c.time}
                             for i, c in enumerate(self._song.cue_points)]}

    def _jump_to_locator(self, locator_index):
        cues = self._song.cue_points
        if locator_index < 0 or locator_index >= len(cues):
            raise IndexError("Locator index out of range")
        cue = cues[locator_index]
        cue.jump()
        return {"jumped_to": cue.name, "time": cue.time}

    def _re_enable_automation(self):
        self._song.re_enable_automation()
        return {"re_enabled": True}

    def _tap_tempo(self):
        self._song.tap_tempo()
        return {"tempo": self._song.tempo}

    def _set_groove_amount(self, amount):
        self._song.groove_amount = max(0.0, min(float(amount), 1.0))
        return {"groove_amount": self._song.groove_amount}

    def _set_swing_amount(self, amount):
        self._song.swing_amount = max(0.0, min(float(amount), 1.0))
        return {"swing_amount": self._song.swing_amount}

    def _jump_by(self, beats):
        self._song.jump_by(float(beats))
        return {"current_song_time": self._song.current_song_time}

    def _jump_to_cue(self, direction):
        if int(direction) < 0:
            self._song.jump_to_prev_cue()
        else:
            self._song.jump_to_next_cue()
        return {"current_song_time": self._song.current_song_time}

    def _set_ableton_link(self, enabled):
        self._song.is_ableton_link_enabled = bool(enabled)
        return {"ableton_link_enabled": self._song.is_ableton_link_enabled}

    def _delete_device(self, track_index, device_index):
        track = self._get_track(track_index)
        if device_index < 0 or device_index >= len(track.devices):
            raise IndexError("Device index out of range")
        track.delete_device(device_index)
        return {"track": track.name, "device_count": len(track.devices)}

    def _create_take_lane(self, track_index):
        track = self._get_track(track_index)
        track.create_take_lane()
        return {"track": track.name, "take_lane_count": len(track.take_lanes)}

    def _snapshot_track_ids(self, current_tracks):
        """Stable IDs with linear lookups for stable/hashable Live wrappers."""
        if not hasattr(self, "_snapshot_scope"):
            self._snapshot_scope = uuid.uuid4().hex
            self._snapshot_track_refs = []
            self._snapshot_next_id = 0
        previous = self._snapshot_track_refs
        by_identity = {id(track): (track, identity) for track, identity in previous}
        by_value, unhashable = {}, []
        for track, identity in previous:
            try:
                by_value[track] = identity
            except (TypeError, RuntimeError, ReferenceError):
                unhashable.append((track, identity))
        current = []
        for track in current_tracks:
            exact = by_identity.get(id(track))
            identity = exact[1] if exact is not None and exact[0] is track else None
            if identity is None:
                try:
                    identity = by_value.get(track)
                    candidates = unhashable
                except (TypeError, RuntimeError, ReferenceError):
                    candidates = previous
                if identity is None:
                    for old, old_identity in candidates:
                        try:
                            if track == old:
                                identity = old_identity
                                break
                        except (RuntimeError, ReferenceError):
                            continue
            if identity is None:
                self._snapshot_next_id += 1
                identity = self._snapshot_scope + ":" + str(self._snapshot_next_id)
            current.append((track, identity))
        self._snapshot_track_refs = current
        return [identity for _, identity in current]

    def _get_session_notes(self):
        """Bounded per-clip reads over multiple ticks, with explicit coverage."""
        original_tracks = tuple(self._song.tracks)
        track_ids = self._snapshot_track_ids(original_tracks)
        active = deque((index, track, iter(track.clip_slots)) for index, track in
                       enumerate(original_tracks[:128]) if track.has_midi_input)
        notes, skipped, used = [], [], set()
        counts = {"clips": 0, "slots": 0}
        reasons = set()
        if len(original_tracks) > 128:
            reasons.add("track_limit")
        def start(finish):
            deadline = time.monotonic() + self._SCAN_TOTAL_SECONDS
            def report():
                return {"notes": notes, "tracks_analyzed": len(used), "clips_analyzed": counts["clips"],
                        "partial": bool(reasons or skipped), "skipped": skipped,
                        "limit_reasons": sorted(reasons),
                        "coverage": "Session MIDI clips; <=128 tracks/clips, 2048 slots and 32000 notes",
                        "consistency": "Per-clip reads across Live ticks; concurrent note edits may be observed"}
            def tick():
                tick_end = time.monotonic() + self._SCAN_TICK_SECONDS
                steps = 0
                try:
                    if self._snapshot_track_ids(tuple(self._song.tracks)) != track_ids:
                        reasons.add("session_changed")
                        finish(result=report())
                        return
                    while active and steps < self._SCAN_STEPS_PER_TICK and time.monotonic() < tick_end:
                        if time.monotonic() >= deadline:
                            reasons.add("scan_budget")
                            break
                        if counts["slots"] >= 2048 or counts["clips"] >= 128 or len(notes) >= 32000:
                            reasons.add("data_limit")
                            break
                        index, track, slots = active.popleft()
                        steps += 1
                        try:
                            slot = next(slots)
                        except StopIteration:
                            continue
                        active.append((index, track, slots))
                        counts["slots"] += 1
                        if not slot.has_clip or not slot.clip.is_midi_clip:
                            continue
                        try:
                            room = 32000 - len(notes)
                            chunk = self._read_all_notes(slot.clip, max_notes=room + 1)
                            notes.extend(chunk[:room])
                            if len(chunk) > room:
                                reasons.add("data_limit")
                            if chunk:
                                used.add(index)
                            counts["clips"] += 1
                        except Exception as exc:
                            skipped.append({"track_index": index, "error": str(exc)})
                    if active and time.monotonic() >= deadline:
                        reasons.add("scan_budget")
                    if not active or reasons.intersection(("scan_budget", "data_limit")):
                        finish(result=report())
                    else:
                        self.schedule_message(1, tick)
                except Exception as exc:
                    finish(error=exc)
            self.schedule_message(1, tick)
        return _Deferred(start)

    def _get_session_snapshot(self):
        song = self._song
        tracks = []
        current_tracks = list(song.tracks)
        identities = self._snapshot_track_ids(current_tracks)
        for i, t in enumerate(current_tracks):
            clips = sum(1 for s in t.clip_slots if s.has_clip)
            vol = None
            try:
                vol = round(t.mixer_device.volume.value, 3)
            except Exception:
                pass
            tracks.append(
                {
                    "index": i,
                    "id": identities[i],
                    "name": t.name,
                    "type": (
                        "midi"
                        if getattr(t, "has_midi_input", False)
                        else "audio"
                        if getattr(t, "has_audio_input", False)
                        else "group"
                    ),
                    "muted": bool(t.mute),
                    "soloed": bool(t.solo),
                    "armed": bool(t.arm) if t.can_be_armed else None,
                    "volume": vol,
                    "clips": clips,
                    "devices": [d.name for d in t.devices],
                }
            )
        return {
            "snapshot_scope": self._snapshot_scope,
            "tempo": song.tempo,
            "time_signature": f"{song.signature_numerator}/{song.signature_denominator}",
            "is_playing": bool(song.is_playing),
            "track_count": len(song.tracks),
            "scene_count": len(song.scenes),
            "return_tracks": [t.name for t in song.return_tracks],
            "tracks": tracks,
        }

    def _set_simpler_playback_mode(self, track_index, device_index, mode):
        device = self._get_device(track_index, device_index)
        if not hasattr(device, "playback_mode"):
            raise Exception(f"Device {device_index} on track {track_index} is not a Simpler")
        device.playback_mode = int(mode)
        return {"device": device.name, "playback_mode": device.playback_mode}

    def _replace_simpler_sample(self, track_index, device_index, path):
        device = self._get_device(track_index, device_index)
        version = tuple(int(part) for part in self._live_version().split(".")[:2])
        if version < (12, 4) or not callable(getattr(device, "replace_sample", None)):
            raise ValueError("Sample replacement requires Live 12.4+ and a Simpler exposing replace_sample")
        if not isinstance(path, str) or not os.path.isabs(path) or not os.path.isfile(path):
            raise ValueError("Provide an absolute path to an existing local audio file")
        device.replace_sample(path)
        sample = getattr(device, "sample", None)
        return {"device": device.name, "requested_path": path,
                "loaded_path": getattr(sample, "file_path", None),
                "note": "Readback may be unavailable; audition and verify in Live."}

    def _get_group_info(self, track_index):
        t = self._get_track(track_index)
        grouped = bool(getattr(t, "is_grouped", False))
        return {
            "name": t.name,
            "is_foldable": bool(t.is_foldable),
            "is_grouped": grouped,
            "fold_state": bool(t.fold_state) if t.is_foldable else None,
            "group_track": t.group_track.name if grouped and t.group_track else None,
        }

    def _set_fold_state(self, track_index, folded):
        t = self._get_track(track_index)
        if not t.is_foldable:
            raise Exception(f"Track '{t.name}' is not a group track")
        t.fold_state = bool(folded)
        return {"name": t.name, "fold_state": bool(t.fold_state)}

    def _try_save_project(self):
        song = self._song
        if not hasattr(song, "save_project"):
            return {"available": False, "note": "song.save_project is not in this Live version's LOM"}
        try:
            song.save_project()
            return {"available": True, "saved": True}
        except Exception as e:
            return {"available": True, "saved": False, "error": str(e)}

    def _get_device_routing(self, track_index, device_index):
        dev = self._get_device(track_index, device_index)
        if not hasattr(dev, "input_routing_type"):
            raise Exception(f"Device '{dev.name}' has no audio input routing (not sidechain-capable)")
        chan = getattr(dev, "input_routing_channel", None)
        return {
            "device": dev.name,
            "input_routing_type": dev.input_routing_type.display_name if dev.input_routing_type else None,
            "available_input_routing_types": [r.display_name for r in dev.available_input_routing_types],
            "input_routing_channel": chan.display_name if chan else None,
            "available_input_routing_channels": [
                c.display_name for c in getattr(dev, "available_input_routing_channels", [])
            ],
        }

    def _set_device_routing(self, track_index, device_index, field, display_name):
        dev = self._get_device(track_index, device_index)
        if field not in ("input_routing_type", "input_routing_channel"):
            raise Exception("field must be input_routing_type or input_routing_channel")
        options = list(getattr(dev, "available_" + field + "s"))
        match = next((o for o in options if o.display_name == display_name), None)
        if match is None:
            raise Exception(
                f"No {field} '{display_name}'. Options: {[o.display_name for o in options]}"
            )
        setattr(dev, field, match)
        return {"device": dev.name, field: getattr(dev, field).display_name}

    def _preview_browser_item(self, item_uri):
        app = self.application()
        browser = getattr(app, "browser", None)
        if browser is None or not hasattr(browser, "preview_item"):
            raise Exception("This Live version's browser does not support preview")
        item = self._find_browser_item_by_uri(browser, item_uri)
        if item is None:
            raise Exception(f"No browser item for uri: {item_uri}")
        try:
            browser.preview_item(item)
        except Exception:
            self._uri_cache.pop(item_uri, None)
            raise
        return {"previewing": item.name}

    def _stop_browser_preview(self):
        browser = getattr(self.application(), "browser", None)
        if browser is not None and hasattr(browser, "stop_preview"):
            browser.stop_preview()
        return {"stopped": True}

    def _get_scale_info(self):
        song = self._song
        return {
            "scale_name": getattr(song, "scale_name", None),
            "root_note": getattr(song, "root_note", None),
            "scale_intervals": list(getattr(song, "scale_intervals", []) or []),
            "scale_mode": bool(getattr(song, "scale_mode", False)),
            "tuning_system": getattr(getattr(song, "tuning_system", None), "name", None),
        }

    def _set_arrangement_overdub(self, enabled):
        self._song.arrangement_overdub = bool(enabled)
        return {"arrangement_overdub": self._song.arrangement_overdub}

    def _set_session_automation_record(self, enabled):
        self._song.session_automation_record = bool(enabled)
        return {"session_automation_record": self._song.session_automation_record}

    def _trigger_session_record(self, record_length):
        if record_length:
            self._song.trigger_session_record(float(record_length))
        else:
            self._song.trigger_session_record()
        return {"triggered": True, "record_length": record_length}

    def _duplicate_clip_to(self, src_track, src_scene, dst_track, dst_scene):
        s = self._get_track(src_track)
        d = self._get_track(dst_track)
        if not (0 <= src_scene < len(s.clip_slots) and 0 <= dst_scene < len(d.clip_slots)):
            raise IndexError("Clip slot index out of range")
        if not s.clip_slots[src_scene].has_clip:
            raise Exception("Source slot has no clip")
        s.clip_slots[src_scene].duplicate_clip_to(d.clip_slots[dst_scene])
        return {"copied": True}

    # ── additional commands ──
    @staticmethod
    def _routing_to_dict(r):
        return {"display_name": r.display_name, "identifier": getattr(r, "identifier", None)}

    _ROUTING_FIELDS = {
        "input_routing_type": "available_input_routing_types",
        "output_routing_type": "available_output_routing_types",
        "input_routing_channel": "available_input_routing_channels",
        "output_routing_channel": "available_output_routing_channels",
    }

    def _get_track_routing(self, track_index):
        t = self._get_track(track_index)
        out = {"monitoring": int(t.current_monitoring_state) if t.can_be_armed else None}
        for field, avail in self._ROUTING_FIELDS.items():
            r = getattr(t, field, None)
            out[field] = self._routing_to_dict(r) if r is not None else None
            out[avail] = [self._routing_to_dict(x) for x in getattr(t, avail, [])]
        return out

    def _set_track_routing(self, track_index, field, display_name):
        t = self._get_track(track_index)
        if field not in self._ROUTING_FIELDS:
            raise ValueError(f"field must be one of {sorted(self._ROUTING_FIELDS)}")
        options = getattr(t, self._ROUTING_FIELDS[field])
        for cand in options:
            if cand.display_name == display_name:
                setattr(t, field, cand)
                return {field: cand.display_name}
        names = [c.display_name for c in options]
        raise ValueError(f"'{display_name}' not available for {field}. Options: {names}")

    def _set_track_monitoring(self, track_index, state):
        t = self._get_track(track_index)
        if not t.can_be_armed:
            raise Exception("Track has no monitoring (not armable)")
        t.current_monitoring_state = int(state)
        return {"monitoring": int(t.current_monitoring_state)}

    # ── additional commands ──
    def _get_clip_info(self, track_index, clip_index):
        clip = self._get_clip(track_index, clip_index)
        info = {"name": clip.name, "length": clip.length,
                "is_midi_clip": clip.is_midi_clip, "is_audio_clip": clip.is_audio_clip,
                "looping": clip.looping, "loop_start": clip.loop_start,
                "loop_end": clip.loop_end, "is_playing": clip.is_playing,
                "playing_position": getattr(clip, "playing_position", None),
                "start_marker": getattr(clip, "start_marker", None),
                "end_marker": getattr(clip, "end_marker", None),
                "color_index": getattr(clip, "color_index", None),
                "signature_numerator": getattr(clip, "signature_numerator", None),
                "signature_denominator": getattr(clip, "signature_denominator", None)}
        if clip.is_audio_clip:
            info.update({"file_path": getattr(clip, "file_path", None),
                         "gain_display": getattr(clip, "gain_display_string", None),
                         "warping": clip.warping,
                         "warp_markers": [{"beat_time": w.beat_time, "sample_time": w.sample_time}
                                          for w in getattr(clip, "warp_markers", [])]})
        return info

    def _clip_op(self, track_index, clip_index, op, params=None):
        clip = self._get_clip(track_index, clip_index)
        if op == "duplicate_loop":
            clip.duplicate_loop()
            return {"length": clip.length}
        if op == "crop":
            clip.crop()
            return {"length": clip.length}
        if op == "duplicate_region":
            p = params or {}
            clip.duplicate_region(float(p["region_start"]), float(p["region_length"]),
                                  float(p["destination_time"]), int(p.get("pitch", -1)),
                                  int(p.get("transposition_amount", 0)))
            return {"length": clip.length}
        raise ValueError("op must be duplicate_loop | crop | duplicate_region")

    def _set_clip_signature(self, track_index, clip_index, numerator, denominator):
        clip = self._get_clip(track_index, clip_index)
        clip.signature_numerator = int(numerator)
        clip.signature_denominator = int(denominator)
        return {"signature": f"{numerator}/{denominator}"}

    # ── additional commands ──
    def _get_rack_chains(self, track_index, device_index):
        device = self._get_device(track_index, device_index)
        if not getattr(device, "can_have_chains", False):
            raise Exception(f"'{device.name}' is not a rack")
        chains = []
        for ci, chain in enumerate(device.chains):
            chains.append({"index": ci, "name": chain.name,
                           "devices": [{"index": di, "name": d.name}
                                       for di, d in enumerate(chain.devices)]})
        return {"rack": device.name, "chains": chains}

    def _set_chain_device_parameter(self, track_index, device_index, chain_index,
                                    chain_device_index, parameter, value):
        rack = self._get_device(track_index, device_index)
        if not getattr(rack, "can_have_chains", False):
            raise Exception(f"'{rack.name}' is not a rack")
        chains = rack.chains
        if not 0 <= chain_index < len(chains):
            raise IndexError("Chain index out of range")
        devices = chains[chain_index].devices
        if not 0 <= chain_device_index < len(devices):
            raise IndexError("Chain device index out of range")
        device = devices[chain_device_index]
        param = self._resolve_parameter(device, parameter)
        if not param.is_enabled:
            raise ValueError("Parameter is disabled: " + param.name)
        requested = value
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                pass
        native, warning = _parameter_value(param, value)
        param.value = native
        result = {"device": device.name, "parameter": param.name, "value": param.value,
                  "native": param.value, "requested": requested, "warning": warning}
        try:
            result["display"] = param.str_for_value(param.value)
        except Exception as exc:
            result["display"] = None
            result["warning"] = "; ".join(part for part in (warning, "Value applied; display readback unavailable: " + str(exc)) if part)
        return result

    def _get_drum_pads(self, track_index, device_index):
        device = self._get_device(track_index, device_index)
        if not getattr(device, "can_have_drum_pads", False):
            raise Exception(f"'{device.name}' is not a drum rack")
        pads = []
        for pad in device.drum_pads:
            if pad.chains:
                pads.append({"note": pad.note, "name": pad.name,
                             "mute": pad.mute, "solo": pad.solo})
        return {"rack": device.name, "pads": pads}

    def _set_drum_pad(self, track_index, device_index, note, params):
        device = self._get_device(track_index, device_index)
        if not getattr(device, "can_have_drum_pads", False):
            raise Exception(f"'{device.name}' is not a drum rack")
        for pad in device.drum_pads:
            if pad.note == int(note):
                applied = {}
                if "mute" in params:
                    pad.mute = bool(params["mute"])
                    applied["mute"] = pad.mute
                if "solo" in params:
                    pad.solo = bool(params["solo"])
                    applied["solo"] = pad.solo
                if "name" in params:
                    pad.name = params["name"]
                    applied["name"] = pad.name
                return applied
        raise Exception(f"No drum pad at note {note}")

    def _rack_variation(self, track_index, device_index, action, index=None):
        rack = self._get_device(track_index, device_index)
        if action == "store":
            rack.store_variation()
        elif action == "recall":
            if index is not None:
                rack.selected_variation_index = int(index)
            rack.recall_selected_variation()
        elif action == "randomize":
            rack.randomize_macros()
        else:
            raise ValueError("action must be store | recall | randomize")
        return {"action": action,
                "variation_count": getattr(rack, "variation_count", None)}

    def _set_crossfader(self, value):
        p = self._song.master_track.mixer_device.crossfader
        p.value = max(p.min, min(p.max, float(value)))
        return {"crossfader": p.value}

    def _set_crossfade_assign(self, track_index, assign):
        t = self._get_track(track_index)
        t.mixer_device.crossfade_assign = int(assign)
        return {"crossfade_assign": int(t.mixer_device.crossfade_assign)}

    def _get_device_type(self, device):
        """Use Live's enum while preserving the rack categories clients know."""
        import Live
        if device.can_have_drum_pads:
            return "drum_machine"
        if device.can_have_chains:
            return "rack"
        device_type = Live.Device.DeviceType
        return {device_type.instrument: "instrument",
                device_type.audio_effect: "audio_effect",
                device_type.midi_effect: "midi_effect"}.get(device.type, "unknown")

    _BROWSER_ROOTS = ("instruments", "sounds", "drums", "audio_effects", "midi_effects",
                      "plugins", "max_for_live", "user_library", "packs", "samples",
                      "clips", "grooves", "current_project")
    _BROWSER_LIST_LIMIT = 1000
    _BROWSER_LOOKUP_NODES = 5000
    _BROWSER_LOOKUP_SECONDS = 0.25
    _BROWSER_CACHE_LIMIT = 1024
    _BROWSER_CACHE_SECONDS = 60.0

    def _browser_roots(self, browser=None, category=None):
        if browser is None:
            browser = getattr(self.application(), "browser", None)
        if browser is None:
            raise RuntimeError("Browser is not available; wait until Live is fully loaded")
        if category and category not in self._BROWSER_ROOTS:
            raise ValueError("Unknown browser category: " + str(category))
        roots, errors = {}, {}
        for name in (category,) if category else self._BROWSER_ROOTS:
            try:
                root = getattr(browser, name, None)
                if root is not None:
                    roots[name] = root
            except Exception as exc:
                errors[name] = str(exc)
        if category and category not in roots:
            raise ValueError("Unavailable browser category '{}': {}".format(
                category, errors.get(category, "not supported by this Live version")))
        return roots, errors

    def _cache_browser_item(self, item):
        uri = getattr(item, "uri", None)
        if uri:
            if not hasattr(self, "_uri_cache"):
                self._uri_cache = OrderedDict()
            self._uri_cache.pop(uri, None)
            self._uri_cache[uri] = (time.monotonic(), item)
            if len(self._uri_cache) > self._BROWSER_CACHE_LIMIT:
                self._uri_cache.popitem(last=False)
        return uri

    def _browser_item_info(self, item, path):
        return {"name": item.name, "path": path,
                "uri": self._cache_browser_item(item),
                "is_folder": bool(getattr(item, "is_folder", False)),
                "is_device": bool(getattr(item, "is_device", False)),
                "is_loadable": bool(getattr(item, "is_loadable", False))}

    def get_browser_tree(self, category_type="all"):
        """List supported roots only; child entries are read through their paths."""
        roots, errors = self._browser_roots(category=category_type if category_type != "all" else None)
        categories = []
        for name, root in roots.items():
            try:
                categories.append(self._browser_item_info(root, name))
            except Exception as exc:
                errors[name] = str(exc)
        return {"type": category_type, "categories": categories, "category_count": len(categories),
                "available_categories": list(roots), "partial": bool(errors), "errors": errors}

    def get_browser_items_at_path(self, path):
        """Resolve a folder and list at most 1,000 immediate entries, with explicit limits."""
        if not isinstance(path, str) or not path.strip("/"):
            raise ValueError("A browser category/folder path is required")
        parts = [part for part in path.split("/") if part]
        roots, _ = self._browser_roots(category=parts[0].lower())
        current = roots[parts[0].lower()]
        deadline, visited = time.monotonic() + self._BROWSER_LOOKUP_SECONDS, 0
        for part in parts[1:]:
            for child in getattr(current, "children", ()):
                visited += 1
                if visited > self._BROWSER_LOOKUP_NODES or time.monotonic() >= deadline:
                    raise RuntimeError("Browser path lookup incomplete: scan budget reached; use search_browser")
                if child.name.lower() == part.lower():
                    current = child
                    break
            else:
                raise ValueError(f"Browser path part '{part}' not found in '{path}'")
        result = self._browser_item_info(current, path)
        items, reasons = [], []
        for child in getattr(current, "children", ()):
            if len(items) >= self._BROWSER_LIST_LIMIT or time.monotonic() >= deadline:
                reasons.append("listing_budget")
                break
            items.append(self._browser_item_info(child, path.rstrip("/") + "/" + child.name))
        result.update(items=items, item_count=len(items), partial=bool(reasons), limit_reasons=reasons)
        return result
