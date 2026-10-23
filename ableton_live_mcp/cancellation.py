"""Cooperative cancellation primitives. Keep this module standard-library-only."""

import threading
from contextlib import contextmanager

_local = threading.local()

# Mirrored by the Live bridge's read-only dispatch table (checked in tests).
READ_COMMANDS = frozenset(
    {
        "get_operation_status",
        "get_session_notes",
        "get_session_info",
        "get_track_info",
        "get_return_tracks",
        "get_arrangement_clips",
        "get_clip_notes",
        "get_grooves",
        "get_device_parameters",
        "get_return_device_parameters",
        "get_master_device_parameters",
        "get_browser_tree",
        "get_browser_items_at_path",
        "search_browser",
        "get_track_meters",
        "get_master_meters",
        "get_locators",
        "get_track_routing",
        "get_clip_info",
        "get_rack_chains",
        "get_drum_pads",
        "get_session_snapshot",
        "get_group_info",
        "get_device_routing",
        "get_scale_info",
    }
)


class ToolCancelled(RuntimeError):
    pass


def check_cancelled():
    event = getattr(_local, "cancel", None)
    if event is not None and event.is_set():
        raise ToolCancelled("Tool cancelled; cleanup was requested")


def check_command_cancelled():
    # An edit that has started must reach its next explicit safe boundary.
    # Recording opts out because it has recovery and explicit cancellation points.
    if not (
        getattr(_local, "finish_started_edits", False)
        and getattr(_local, "mutation_started", False)
    ):
        check_cancelled()


def command_will_send(command_type):
    check_command_cancelled()
    if command_type not in READ_COMMANDS:
        _local.mutation_started = True


def cancellable_wait(seconds):
    event = getattr(_local, "cancel", None) or threading.Event()
    if event.wait(seconds):
        check_cancelled()


@contextmanager
def operation_scope(cancel, *, finish_started_edits=False):
    previous = dict(vars(_local))
    _local.cancel = cancel
    _local.finish_started_edits = finish_started_edits
    _local.mutation_started = False
    try:
        yield
    finally:
        vars(_local).clear()
        vars(_local).update(previous)


@contextmanager
def cleanup_scope():
    event = getattr(_local, "cancel", None)
    _local.cancel = None
    try:
        yield
    finally:
        _local.cancel = event
