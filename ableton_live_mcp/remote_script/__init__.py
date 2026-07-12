"""AbletonMCP Remote Script: the Live-side half of the server.

This module runs inside Ableton Live (imported by Live's Remote Script host, not
by the MCP server). `ableton-live-mcp install` copies this file into the User
Library as Remote Scripts/AbletonMCP/__init__.py. It opens a loopback socket on
port 9877 and dispatches JSON commands to the Live API on the main thread.
"""

import codecs
import json
import os
import queue
import socket
import threading
import time
import traceback

from _Framework.ControlSurface import ControlSurface

# Socket the script listens on. Bind loopback only by default - this port drives
# Live with no auth, so it must not be exposed to the network. If 9877 is taken,
# set ABLETON_MCP_PORT (and the client's ABLETON_PORT to match). ABLETON_MCP_HOST
# overrides the bind address for advanced setups (e.g. containers) at your own risk.
DEFAULT_PORT = int(os.environ.get("ABLETON_MCP_PORT", "9877"))
HOST = os.environ.get("ABLETON_MCP_HOST", "127.0.0.1")
MAX_REQUEST_BYTES = 10 * 1024 * 1024  # backstop against a poisoned request buffer

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

                except TimeoutError:
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
        "create_locator": lambda s, p: s._create_locator(p.get("name")),
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
        "set_fold_state": lambda s, p: s._set_fold_state(s._req(p, "track_index"), p.get("folded", True)),
        "try_save_project": lambda s, p: s._try_save_project(),
        "set_device_routing": lambda s, p: s._set_device_routing(s._req(p, "track_index"), s._req(p, "device_index"), s._req(p, "field"), s._req(p, "display_name")),
        "preview_browser_item": lambda s, p: s._preview_browser_item(s._req(p, "item_uri")),
        "stop_browser_preview": lambda s, p: s._stop_browser_preview(),
    }

    _READONLY_COMMANDS = {
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
        """Process a command from the client and return a response"""
        command_type = command.get("type", "")
        params = command.get("params", {})
        response = {"status": "success", "result": {}}

        try:
            handler = self._ALL_COMMANDS.get(command_type)
            if handler is None:
                response["status"] = "error"
                response["message"] = "Unknown command: " + command_type
                return response

            # ALL Live Object Model access runs on Live's main thread - reading
            # deep state (tracks, clips, devices) from a socket thread races
            # Live's audio/UI threads and can crash. The socket threads only do
            # I/O; this queue bridges them to a scheduled main-thread task.
            response_queue = queue.Queue()
            cancelled = threading.Event()

            def main_thread_task():
                if cancelled.is_set():
                    # The client already received a timeout error; applying the
                    # mutation late (after newer commands) would reorder edits.
                    self.log_message("Skipping cancelled task: " + command_type)
                    return
                try:
                    response_queue.put({"status": "success",
                                        "result": handler(self, params)})
                except Exception as e:
                    self.log_message("Error in main thread task: " + str(e))
                    self.log_message(traceback.format_exc())
                    response_queue.put({"status": "error", "message": str(e)})

            if threading.current_thread() is self._main_thread:
                # Already on the main thread - execute directly.
                main_thread_task()
            else:
                self.schedule_message(0, main_thread_task)

            timeout = self._COMMAND_TIMEOUTS.get(command_type, 10.0)
            if command_type == "batch":
                # A batch inherits the longest budget of its sub-commands.
                for cmd in params.get("commands") or []:
                    sub = cmd.get("type", "") if isinstance(cmd, dict) else ""
                    sub = self._BATCH_ALIASES.get(sub, sub)
                    timeout = max(timeout, self._COMMAND_TIMEOUTS.get(sub, 0.0))
            try:
                task_response = response_queue.get(timeout=timeout)
                if task_response.get("status") == "error":
                    response["status"] = "error"
                    response["message"] = task_response.get("message", "Unknown error")
                else:
                    response["result"] = task_response.get("result", {})
            except queue.Empty:
                cancelled.set()
                response["status"] = "error"
                response["message"] = ("Timeout waiting for operation to complete "
                                       "(a modal dialog in Live blocks the Remote Script); "
                                       "the operation was cancelled and will not apply late")
        except Exception as e:
            self.log_message("Error processing command: " + str(e))
            self.log_message(traceback.format_exc())
            response["status"] = "error"
            response["message"] = str(e)

        if response["status"] == "error":
            response.pop("result", None)
        return response

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
            self._live_version_cache = f"{app.get_major_version()}.{app.get_minor_version()}"
        return self._live_version_cache

    def _get_session_info(self):
        """Get information about the current session"""
        try:
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
            }
            return result
        except Exception as e:
            self.log_message("Error getting session info: " + str(e))
            raise

    def _get_track_info(self, track_index):
        """Get information about a track"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

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
        except Exception as e:
            self.log_message("Error getting track info: " + str(e))
            raise

    def _create_midi_track(self, index):
        """Create a new MIDI track at the specified index"""
        try:
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
        except Exception as e:
            self.log_message("Error creating MIDI track: " + str(e))
            raise


    def _set_track_name(self, track_index, name):
        """Set the name of a track"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            # Set the name
            track = self._song.tracks[track_index]
            track.name = name

            result = {
                "name": track.name
            }
            return result
        except Exception as e:
            self.log_message("Error setting track name: " + str(e))
            raise

    def _create_clip(self, track_index, clip_index, length):
        """Create a new MIDI clip in the specified track and clip slot"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

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
        except Exception as e:
            self.log_message("Error creating clip: " + str(e))
            raise

    def _create_audio_clip(self, track_index, clip_index, path):
        """Create an audio clip in the specified audio track clip slot by importing a file.

        Requires Ableton Live 12.0.5 or newer (the underlying
        ClipSlot.create_audio_clip Live API was introduced in 12.0.5 - it is
        not available in earlier 12.0.x releases).
        """
        try:
            if not path:
                raise ValueError("Audio file path is required")

            if not os.path.isabs(path):
                raise ValueError(f"Audio file path must be absolute (got: {path})")

            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

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
        except Exception as e:
            self.log_message("Error creating audio clip: " + str(e))
            raise

    def _add_notes_to_clip(self, track_index, clip_index, notes):
        """Add MIDI notes to a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            if not clip_slot.has_clip:
                raise Exception("No clip in slot")

            clip = clip_slot.clip
            if not clip.is_midi_clip:
                raise Exception("Clip is not a MIDI clip")

            # REPLACE the clip's note content (see _replace_all_notes)
            self._replace_all_notes(clip, notes)

            result = {
                "note_count": len(notes)
            }
            return result
        except Exception as e:
            self.log_message("Error adding notes to clip: " + str(e))
            raise

    def _set_clip_name(self, track_index, clip_index, name):
        """Set the name of a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            if not clip_slot.has_clip:
                raise Exception("No clip in slot")

            clip = clip_slot.clip
            clip.name = name

            result = {
                "name": clip.name
            }
            return result
        except Exception as e:
            self.log_message("Error setting clip name: " + str(e))
            raise

    def _set_tempo(self, tempo):
        """Set the tempo of the session"""
        try:
            self._song.tempo = tempo

            result = {
                "tempo": self._song.tempo
            }
            return result
        except Exception as e:
            self.log_message("Error setting tempo: " + str(e))
            raise

    def _fire_clip(self, track_index, clip_index):
        """Fire a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

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
        except Exception as e:
            self.log_message("Error firing clip: " + str(e))
            raise

    def _stop_clip(self, track_index, clip_index):
        """Stop a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            clip_slot.stop()

            result = {
                "stopped": True
            }
            return result
        except Exception as e:
            self.log_message("Error stopping clip: " + str(e))
            raise


    def _start_playback(self):
        """Start playing the session"""
        try:
            self._song.start_playing()

            result = {
                "playing": self._song.is_playing
            }
            return result
        except Exception as e:
            self.log_message("Error starting playback: " + str(e))
            raise

    def _stop_playback(self):
        """Stop playing the session"""
        try:
            self._song.stop_playing()

            result = {
                "playing": self._song.is_playing
            }
            return result
        except Exception as e:
            self.log_message("Error stopping playback: " + str(e))
            raise

    # ── Arrangement view implementations ──────────────────────────────────────

    def _switch_to_arrangement_view(self):
        """Switch Ableton's main window to the Arrangement view"""
        try:
            self.application().view.show_view("Arranger")
            return {"view": "Arranger"}
        except Exception as e:
            self.log_message("Error switching to arrangement view: " + str(e))
            raise

    def _set_current_song_time(self, time_val):
        """Move the arrangement playhead to a position in beats"""
        try:
            self._song.current_song_time = float(time_val)
            return {"current_song_time": self._song.current_song_time}
        except Exception as e:
            self.log_message("Error setting current song time: " + str(e))
            raise

    def _get_arrangement_clips(self, track_index):
        """Return all clips placed in the Arrangement timeline for a track.

        Each clip dict contains:
          name, start_time, end_time, length, color,
          is_midi_clip, is_audio_clip, is_playing
        """
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]
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
        except Exception as e:
            self.log_message("Error getting arrangement clips: " + str(e))
            raise

    def _duplicate_session_clip_to_arrangement(self, track_index, clip_index, destination_time):
        """Copy a Session-view clip into the Arrangement timeline.

        Uses the real Live API:
          track.duplicate_clip_to_arrangement(clip, destination_time)

        Available in Live 11 / 12.  destination_time is in beats from the
        start of the arrangement.
        """
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

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
        except Exception as e:
            self.log_message("Error duplicating clip to arrangement: " + str(e))
            raise

    # ── Browser implementations ───────────────────────────────────────────────

    def _load_browser_item(self, track_index, item_uri):
        """Load a browser item onto a regular track by its URI"""
        try:
            track = self._get_track(track_index)
            item = self._load_uri_onto_track(track, item_uri)
            return {
                "loaded": True,
                "item_name": item.name,
                "track_name": track.name,
                "uri": item_uri,
            }
        except Exception as e:
            self.log_message(f"Error loading browser item: {str(e)}")
            self.log_message(traceback.format_exc())
            raise

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
        """Find a browser item by its URI.

        Top-level lookups are memoised on ``self._uri_cache`` so repeated
        loads of the same URI don't re-walk the entire browser tree.
        """
        if current_depth == 0:
            cache = getattr(self, '_uri_cache', None)
            if cache is None:
                self._uri_cache = cache = {}
            if uri in cache:
                return cache[uri]
            result = self._walk_browser_for_uri(browser_or_item, uri, max_depth, 0)
            if result is not None:
                cache[uri] = result
            return result
        return self._walk_browser_for_uri(browser_or_item, uri, max_depth, current_depth)

    def _walk_browser_for_uri(self, browser_or_item, uri, max_depth, current_depth):
        """Recursive walk used by :py:meth:`_find_browser_item_by_uri`."""
        try:
            # Check if this is the item we're looking for
            if hasattr(browser_or_item, 'uri') and browser_or_item.uri == uri:
                return browser_or_item

            # Stop recursion if we've reached max depth
            if current_depth >= max_depth:
                return None

            # Check if this is a browser with root categories
            if hasattr(browser_or_item, 'instruments'):
                roots = [
                    ('instruments', browser_or_item.instruments),
                    ('sounds', browser_or_item.sounds),
                    ('drums', browser_or_item.drums),
                    ('audio_effects', browser_or_item.audio_effects),
                    ('midi_effects', browser_or_item.midi_effects),
                ]
                for extra_attr in ('plugins', 'max_for_live', 'user_library', 'packs', 'samples'):
                    if hasattr(browser_or_item, extra_attr):
                        try:
                            roots.append((extra_attr, getattr(browser_or_item, extra_attr)))
                        except (AttributeError, RuntimeError) as e:
                            self.log_message(f"Could not access browser.{extra_attr}: {str(e)}")

                for _attr, category in self._order_roots_by_uri(roots, uri):
                    item = self._find_browser_item_by_uri(category, uri, max_depth, current_depth + 1)
                    if item:
                        return item

                return None

            # Check if this item has children
            if hasattr(browser_or_item, 'children') and browser_or_item.children:
                for child in browser_or_item.children:
                    item = self._find_browser_item_by_uri(child, uri, max_depth, current_depth + 1)
                    if item:
                        return item

            return None
        except Exception as e:
            self.log_message(f"Error finding browser item by URI: {str(e)}")
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
        param.value = max(param.min, min(param.max, float(value)))
        result = {"device": device.name, "parameter": param.name, "value": param.value}
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
        track = self._get_track(track_index)
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        slot = track.clip_slots[clip_index]
        if not slot.has_clip:
            raise Exception("No clip in slot")
        clip = slot.clip
        if not clip.is_midi_clip:
            raise Exception("Not a MIDI clip")
        notes = self._read_all_notes(clip)
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
        return {"index": new_index, "track_count": len(self._song.tracks)}

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
        clip = self._get_clip(track_index, clip_index)
        device = self._get_device(track_index, device_index)
        param = self._resolve_parameter(device, parameter)
        # Live only creates a clip envelope for the currently-viewed detail clip.
        try:
            self._song.view.detail_clip = clip
        except Exception:
            pass
        # Clear any existing automation for this param first (clip-level method).
        try:
            clip.clear_envelope(param)
        except Exception:
            pass
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
    def _create_locator(self, name):
        """Create/rename a locator at the CURRENT playhead. Set the playhead with
        set_current_song_time first (as a separate command) so it has settled -
        set_or_delete_cue reads the live playhead, which does not update within
        the same main-thread task that assigns current_song_time."""
        t = float(self._song.current_song_time)
        for cue in self._song.cue_points:
            if abs(cue.time - t) < 1e-3:
                if name:
                    cue.name = name
                return {"time": t, "name": cue.name, "created": False}
        self._song.set_or_delete_cue()
        for cue in self._song.cue_points:
            if abs(cue.time - t) < 1e-3:
                if name:
                    cue.name = name
                return {"time": t, "name": cue.name, "created": True}
        return {"time": t, "name": name, "created": True,
                "note": "locator created; name applies once Live's cue list settles"}

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
        self._song.view.selected_track = track
        app.browser.load_item(item)
        return item

    def _load_device_to_return(self, return_index, item_uri):
        item = self._load_uri_onto_track(self._resolve_track(return_index, "return"), item_uri)
        return {"loaded": True, "item_name": item.name, "return": return_index}

    def _load_device_to_master(self, item_uri):
        item = self._load_uri_onto_track(self._song.master_track, item_uri)
        return {"loaded": True, "item_name": item.name, "track": "master"}

    # -- note-level editing --
    def _edit_notes(self, track_index, clip_index, add, remove):
        clip = self._get_clip(track_index, clip_index)
        if not clip.is_midi_clip:
            raise Exception("Not a MIDI clip")
        if hasattr(clip, "remove_notes_extended") and hasattr(clip, "add_new_notes"):
            # Targeted O(adds+removes) edit - no full rewrite.
            removed = 0
            for rm in remove:
                pitch = int(rm.get("pitch", -1))
                start = float(rm.get("start_time", -1))
                before = len(clip.get_notes_extended(pitch, 1, start - 1e-3, 2e-3))
                clip.remove_notes_extended(pitch, 1, start - 1e-3, 2e-3)
                removed += before
            if add:
                clip.add_new_notes(tuple(self._make_note_spec(n) for n in add))
            total = len(clip.get_all_notes_extended())
            return {"note_count": total, "added": len(add), "removed": removed}
        # legacy fallback: read-filter-rewrite
        existing = self._read_all_notes(clip)

        def matches(note, rm):
            return (abs(note["start_time"] - rm.get("start_time", -1)) < 1e-3
                    and note["pitch"] == rm.get("pitch", -1))

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

    # -- batch: N commands, one main-thread hop, one undo step --
    def _batch(self, commands):
        """Run a list of {type, params} commands atomically-ish: executed in one
        main-thread task, wrapped in a single undo step. Stops at first error."""
        results = []
        self._song.begin_undo_step()
        try:
            for i, cmd in enumerate(commands):
                ctype = self._BATCH_ALIASES.get(cmd.get("type", ""), cmd.get("type", ""))
                handler = self._ALL_COMMANDS.get(ctype)
                if handler is None:
                    raise Exception(f"batch[{i}]: unknown command '{ctype}'")
                if ctype == "batch":
                    raise Exception("batch cannot nest")
                results.append({"type": ctype, "result": handler(self, cmd.get("params", {}))})
        except Exception as e:
            raise Exception(f"batch failed at step {len(results)} of {len(commands)}: {e} "
                            f"(completed steps are one undo away)")
        finally:
            self._song.end_undo_step()
        return {"executed": len(results), "results": results}

    # -- browser search by display name --
    def _search_browser(self, query, category, max_results):
        app = self.application()
        roots = []
        if category:
            root = getattr(app.browser, category, None)
            if root is None:
                raise ValueError(f"Unknown browser category: {category}")
            roots = [root]
        else:
            for name in ("instruments", "sounds", "drums", "audio_effects", "midi_effects", "packs"):
                r = getattr(app.browser, name, None)
                if r is not None:
                    roots.append(r)
        q = query.lower()
        matches = []

        def walk(item, depth):
            if len(matches) >= max_results or depth > 8:
                return
            try:
                children = item.children
            except Exception:
                children = []
            for child in children:
                if len(matches) >= max_results:
                    return
                try:
                    if q in child.name.lower() and child.is_loadable:
                        matches.append({"name": child.name, "uri": child.uri,
                                        "is_device": child.is_device})
                    if child.is_folder:
                        walk(child, depth + 1)
                except Exception:
                    pass

        for root in roots:
            walk(root, 0)
        return {"query": query, "match_count": len(matches), "matches": matches}

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
    def _make_note_spec(n):
        import Live
        kwargs = dict(
            pitch=int(n.get("pitch", 60)),
            start_time=float(n.get("start_time", 0.0)),
            duration=float(n.get("duration", 0.25)),
            velocity=float(n.get("velocity", 100)),
            mute=bool(n.get("mute", False)),
        )
        # Live-11 extended fields are constructor kwargs (not settable after).
        for field in ("probability", "velocity_deviation", "release_velocity"):
            if field in n:
                kwargs[field] = float(n[field])
        try:
            return Live.Clip.MidiNoteSpecification(**kwargs)
        except TypeError:
            # Older build without the extended kwargs - drop them.
            for field in ("probability", "velocity_deviation", "release_velocity"):
                kwargs.pop(field, None)
            return Live.Clip.MidiNoteSpecification(**kwargs)

    def _replace_all_notes(self, clip, note_dicts):
        """Truly REPLACE a clip's notes. Legacy set_notes only ADDS (per LOM docs),
        so clear first via remove_notes_extended when available."""
        if hasattr(clip, "remove_notes_extended") and hasattr(clip, "add_new_notes"):
            clip.remove_notes_extended(0, 128, -(2 ** 14), 2 ** 15)
            specs = []
            for n in note_dicts:
                specs.append(self._make_note_spec(n))
            clip.add_new_notes(tuple(specs))
        else:
            clip.set_notes(tuple(
                (int(n.get("pitch", 60)), float(n.get("start_time", 0.0)),
                 float(n.get("duration", 0.25)), int(n.get("velocity", 100)),
                 bool(n.get("mute", False))) for n in note_dicts))

    def _read_all_notes(self, clip):
        """Read notes via the extended API (incl. probability/deviation) when available."""
        if hasattr(clip, "get_all_notes_extended"):
            out = []
            for n in clip.get_all_notes_extended():
                out.append({"pitch": n.pitch, "start_time": n.start_time,
                            "duration": n.duration, "velocity": n.velocity,
                            "mute": n.mute,
                            "probability": getattr(n, "probability", 1.0),
                            "velocity_deviation": getattr(n, "velocity_deviation", 0.0)})
            return out
        raw = clip.get_notes(0.0, 0, clip.length, 128)
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

    def _get_session_snapshot(self):
        song = self._song
        tracks = []
        for i, t in enumerate(song.tracks):
            clips = sum(1 for s in t.clip_slots if s.has_clip)
            vol = None
            try:
                vol = round(t.mixer_device.volume.value, 3)
            except Exception:
                pass
            tracks.append(
                {
                    "index": i,
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
        browser.preview_item(item)
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
        param.value = max(param.min, min(param.max, float(value)))
        return {"device": device.name, "parameter": param.name, "value": param.value}

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
        """Get the type of a device"""
        try:
            # Simple heuristic - in a real implementation you'd look at the device class
            if device.can_have_drum_pads:
                return "drum_machine"
            elif device.can_have_chains:
                return "rack"
            elif "instrument" in device.class_display_name.lower():
                return "instrument"
            elif "audio_effect" in device.class_name.lower():
                return "audio_effect"
            elif "midi_effect" in device.class_name.lower():
                return "midi_effect"
            else:
                return "unknown"
        except:
            return "unknown"

    def get_browser_tree(self, category_type="all"):
        """
        Get a simplified tree of browser categories.

        Args:
            category_type: Type of categories to get ('all', 'instruments', 'sounds', etc.)

        Returns:
            Dictionary with the browser tree structure
        """
        try:
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")

            # Check if browser is available
            if not hasattr(app, 'browser') or app.browser is None:
                raise RuntimeError("Browser is not available in the Live application")

            # Log available browser attributes to help diagnose issues
            browser_attrs = [attr for attr in dir(app.browser) if not attr.startswith('_')]
            self.log_message(f"Available browser attributes: {browser_attrs}")

            result = {
                "type": category_type,
                "categories": [],
                "available_categories": browser_attrs
            }

            # Helper function to process a browser item and its children
            def process_item(item, depth=0):
                if not item:
                    return None

                result = {
                    "name": item.name if hasattr(item, 'name') else "Unknown",
                    "is_folder": hasattr(item, 'children') and bool(item.children),
                    "is_device": hasattr(item, 'is_device') and item.is_device,
                    "is_loadable": hasattr(item, 'is_loadable') and item.is_loadable,
                    "uri": item.uri if hasattr(item, 'uri') else None,
                    "children": []
                }


                return result

            # Process based on category type and available attributes
            if (category_type == "all" or category_type == "instruments") and hasattr(app.browser, 'instruments'):
                try:
                    instruments = process_item(app.browser.instruments)
                    if instruments:
                        instruments["name"] = "Instruments"  # Ensure consistent naming
                        result["categories"].append(instruments)
                except Exception as e:
                    self.log_message(f"Error processing instruments: {str(e)}")

            if (category_type == "all" or category_type == "sounds") and hasattr(app.browser, 'sounds'):
                try:
                    sounds = process_item(app.browser.sounds)
                    if sounds:
                        sounds["name"] = "Sounds"  # Ensure consistent naming
                        result["categories"].append(sounds)
                except Exception as e:
                    self.log_message(f"Error processing sounds: {str(e)}")

            if (category_type == "all" or category_type == "drums") and hasattr(app.browser, 'drums'):
                try:
                    drums = process_item(app.browser.drums)
                    if drums:
                        drums["name"] = "Drums"  # Ensure consistent naming
                        result["categories"].append(drums)
                except Exception as e:
                    self.log_message(f"Error processing drums: {str(e)}")

            if (category_type == "all" or category_type == "audio_effects") and hasattr(app.browser, 'audio_effects'):
                try:
                    audio_effects = process_item(app.browser.audio_effects)
                    if audio_effects:
                        audio_effects["name"] = "Audio Effects"  # Ensure consistent naming
                        result["categories"].append(audio_effects)
                except Exception as e:
                    self.log_message(f"Error processing audio_effects: {str(e)}")

            if (category_type == "all" or category_type == "midi_effects") and hasattr(app.browser, 'midi_effects'):
                try:
                    midi_effects = process_item(app.browser.midi_effects)
                    if midi_effects:
                        midi_effects["name"] = "MIDI Effects"
                        result["categories"].append(midi_effects)
                except Exception as e:
                    self.log_message(f"Error processing midi_effects: {str(e)}")

            # Try to process other potentially available categories
            for attr in browser_attrs:
                if attr not in ['instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects'] and \
                   (category_type == "all" or category_type == attr):
                    try:
                        item = getattr(app.browser, attr)
                        if hasattr(item, 'children') or hasattr(item, 'name'):
                            category = process_item(item)
                            if category:
                                category["name"] = attr.capitalize()
                                result["categories"].append(category)
                    except Exception as e:
                        self.log_message(f"Error processing {attr}: {str(e)}")

            self.log_message("Browser tree generated for {} with {} root categories".format(
                category_type, len(result['categories'])))
            return result

        except Exception as e:
            self.log_message(f"Error getting browser tree: {str(e)}")
            self.log_message(traceback.format_exc())
            raise

    def get_browser_items_at_path(self, path):
        """
        Get browser items at a specific path.

        Args:
            path: Path in the format "category/folder/subfolder"
                 where category is one of: instruments, sounds, drums, audio_effects, midi_effects
                 or any other available browser category

        Returns:
            Dictionary with items at the specified path
        """
        try:
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")

            # Check if browser is available
            if not hasattr(app, 'browser') or app.browser is None:
                raise RuntimeError("Browser is not available in the Live application")

            # Log available browser attributes to help diagnose issues
            browser_attrs = [attr for attr in dir(app.browser) if not attr.startswith('_')]
            self.log_message(f"Available browser attributes: {browser_attrs}")

            # Parse the path
            path_parts = path.split("/")
            if not path_parts:
                raise ValueError("Invalid path")

            # Determine the root category
            root_category = path_parts[0].lower()
            current_item = None

            # Check standard categories first
            if root_category == "instruments" and hasattr(app.browser, 'instruments'):
                current_item = app.browser.instruments
            elif root_category == "sounds" and hasattr(app.browser, 'sounds'):
                current_item = app.browser.sounds
            elif root_category == "drums" and hasattr(app.browser, 'drums'):
                current_item = app.browser.drums
            elif root_category == "audio_effects" and hasattr(app.browser, 'audio_effects'):
                current_item = app.browser.audio_effects
            elif root_category == "midi_effects" and hasattr(app.browser, 'midi_effects'):
                current_item = app.browser.midi_effects
            else:
                # Try to find the category in other browser attributes
                found = False
                for attr in browser_attrs:
                    if attr.lower() == root_category:
                        try:
                            current_item = getattr(app.browser, attr)
                            found = True
                            break
                        except Exception as e:
                            self.log_message(f"Error accessing browser attribute {attr}: {str(e)}")

                if not found:
                    # If we still haven't found the category, return available categories
                    return {
                        "path": path,
                        "error": f"Unknown or unavailable category: {root_category}",
                        "available_categories": browser_attrs,
                        "items": []
                    }

            # Navigate through the path
            for i in range(1, len(path_parts)):
                part = path_parts[i]
                if not part:  # Skip empty parts
                    continue

                if not hasattr(current_item, 'children'):
                    return {
                        "path": path,
                        "error": "Item at '{}' has no children".format('/'.join(path_parts[:i])),
                        "items": []
                    }

                found = False
                for child in current_item.children:
                    if hasattr(child, 'name') and child.name.lower() == part.lower():
                        current_item = child
                        found = True
                        break

                if not found:
                    return {
                        "path": path,
                        "error": f"Path part '{part}' not found",
                        "items": []
                    }

            # Get items at the current path
            items = []
            if hasattr(current_item, 'children'):
                for child in current_item.children:
                    item_info = {
                        "name": child.name if hasattr(child, 'name') else "Unknown",
                        "is_folder": hasattr(child, 'children') and bool(child.children),
                        "is_device": hasattr(child, 'is_device') and child.is_device,
                        "is_loadable": hasattr(child, 'is_loadable') and child.is_loadable,
                        "uri": child.uri if hasattr(child, 'uri') else None
                    }
                    items.append(item_info)

            result = {
                "path": path,
                "name": current_item.name if hasattr(current_item, 'name') else "Unknown",
                "uri": current_item.uri if hasattr(current_item, 'uri') else None,
                "is_folder": hasattr(current_item, 'children') and bool(current_item.children),
                "is_device": hasattr(current_item, 'is_device') and current_item.is_device,
                "is_loadable": hasattr(current_item, 'is_loadable') and current_item.is_loadable,
                "items": items
            }

            self.log_message(f"Retrieved {len(items)} items at path: {path}")
            return result

        except Exception as e:
            self.log_message(f"Error getting browser items at path: {str(e)}")
            self.log_message(traceback.format_exc())
            raise
