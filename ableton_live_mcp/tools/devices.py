"""Device loading and parameter control, incl. Master/Return chains."""

import math

from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import (
    BrowserItemUri,
    DeviceIndex,
    DeviceParameter,
    DeviceParameterValue,
    ReturnIndex,
    ToggleState,
    TrackIndex,
    params,
)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def load_instrument_or_effect(track_index: int, uri: str) -> dict:
    """
    Load an instrument or effect onto a track using its URI.

    Parameters:
    - track_index: The index of the track to load the instrument on
    - uri: The URI of the instrument or effect to load (e.g., 'query:Synths#Instrument%20Rack:Bass:FileId_5116')
    """
    return get_ableton_connection().send_command(
        "load_browser_item", {"track_index": track_index, "item_uri": uri}
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_device_parameters(track_index: int, device_index: int) -> dict:
    """List all parameters of a device on a track: names, values, ranges, display strings."""
    return get_ableton_connection().send_command(
        "get_device_parameters", {"track_index": track_index, "device_index": device_index}
    )


def _set_param(command: str, **wire_params) -> dict:
    conn = get_ableton_connection()
    if command == "set_chain_device_parameter" and isinstance(wire_params.get("value"), str):
        try:
            native = float(wire_params["value"])
        except ValueError:
            conn.require_capability("chain_display_values")
        else:
            if not math.isfinite(native):
                raise ValueError("Parameter value must be finite")
    return conn.send_command(command, wire_params)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_device_parameter(
    track_index: TrackIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    value: DeviceParameterValue,
) -> dict:
    """Set a parameter after reading get_device_parameters. Numbers use the native range;
    strings use display units ('250 Hz', '-6 dB') or exact enum labels. Unsupported
    display mappings fail before writing. Returns Live's actual readback and warnings."""
    return _set_param(
        "set_device_parameter",
        track_index=track_index,
        device_index=device_index,
        parameter=parameter,
        value=value,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_device_enabled(
    track_index: TrackIndex,
    device_index: DeviceIndex,
    enabled: ToggleState,
) -> dict:
    """Enable or bypass one device on a regular track.

    `true` turns the device on; `false` bypasses its processing while preserving
    the device and parameter values. This changes audible signal flow but not
    clip content. Use set_track_mute to silence the whole track, or
    set_device_parameter to change one control.
    """
    return get_ableton_connection().send_command(
        "set_device_enabled",
        {"track_index": track_index, "device_index": device_index, "enabled": enabled},
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def load_device_to_return(return_index: ReturnIndex, item_uri: BrowserItemUri) -> dict:
    """Append one loadable browser device or preset to a return track's chain.

    Obtain `item_uri` from search_browser. Loading selects the return track in
    Live and creates a new device, so repeated calls add duplicates. Prefer audio
    effects on returns; use load_instrument_or_effect for a regular track or
    load_device_to_master for the Master chain.
    """
    return get_ableton_connection().send_command(
        "load_device_to_return", {"return_index": return_index, "item_uri": item_uri}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def load_device_to_master(item_uri: str) -> dict:
    """Load a device/effect from the browser onto the Master/Main track (e.g. a limiter for mastering)."""
    return get_ableton_connection().send_command("load_device_to_master", {"item_uri": item_uri})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_master_device_parameters(device_index: int) -> dict:
    """List parameters of a device on the Master track."""
    return get_ableton_connection().send_command(
        "get_master_device_parameters", {"device_index": device_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_master_device_parameter(
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    value: DeviceParameterValue,
) -> dict:
    """Set one enabled parameter on a device in the Master track's chain.

    Call get_master_device_parameters first and use its parameter name/index and
    native min/max; numeric out-of-range values are clamped. Display strings with
    supported units or exact enum labels also work; unsupported mappings fail
    before writing. This affects the full mix. Read back the resulting display.
    Use set_device_parameter for a regular track or
    set_return_device_parameter for a return track.
    """
    return _set_param(
        "set_master_device_parameter",
        device_index=device_index,
        parameter=parameter,
        value=value,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def set_return_device_parameter(
    return_index: ReturnIndex,
    device_index: DeviceIndex,
    parameter: DeviceParameter,
    value: DeviceParameterValue,
) -> dict:
    """Set one enabled parameter on a device in a return track's chain.

    Call get_return_device_parameters first and use its parameter name/index and
    native min/max; numeric out-of-range values are clamped. Supported display-unit
    strings and exact enum labels also work; unsupported mappings fail before writing.
    Use set_device_parameter for
    a regular track or set_master_device_parameter for the full-mix chain.
    """
    return _set_param(
        "set_return_device_parameter",
        return_index=return_index,
        device_index=device_index,
        parameter=parameter,
        value=value,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_return_device_parameters(return_index: int, device_index: int) -> dict:
    """List parameters of a device on a Return track: names, values, min/max,
    display strings. Call before set_return_device_parameter."""
    return get_ableton_connection().send_command(
        "get_return_device_parameters",
        {"return_index": return_index, "device_index": device_index},
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_rack_chains(track_index: int, device_index: int) -> dict:
    """List an Instrument/Effect Rack's chains and the devices inside each -
    previously unreachable nested devices. Use set_chain_device_parameter to
    control them."""

    return get_ableton_connection().send_command(
        "get_rack_chains", {"track_index": track_index, "device_index": device_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_chain_device_parameter(
    track_index: int,
    device_index: int,
    chain_index: int,
    chain_device_index: int,
    parameter: str | int,
    value: DeviceParameterValue,
) -> dict:
    """Set a parameter on a device INSIDE a rack chain (indices from
    get_rack_chains). Numbers use native values; display strings and enum labels use the same conversion as top-level devices."""
    return _set_param(
        "set_chain_device_parameter",
        track_index=track_index,
        device_index=device_index,
        chain_index=chain_index,
        chain_device_index=chain_device_index,
        parameter=parameter,
        value=value,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_drum_pads(track_index: int, device_index: int) -> dict:
    """List a Drum Rack's occupied pads (MIDI note, name, mute, solo)."""

    return get_ableton_connection().send_command(
        "get_drum_pads", {"track_index": track_index, "device_index": device_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_drum_pad(
    track_index: int,
    device_index: int,
    note: int,
    mute: bool | None = None,
    solo: bool | None = None,
    name: str | None = None,
) -> dict:
    """Mute/solo/rename a single Drum Rack pad by its MIDI note (e.g. 36=kick).
    Per-pad muting = instant beat variations without touching the notes."""
    return get_ableton_connection().send_command(
        "set_drum_pad",
        params(
            track_index=track_index,
            device_index=device_index,
            note=note,
            mute=mute,
            solo=solo,
            name=name,
        ),
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def rack_variation(
    track_index: int, device_index: int, action: str, index: int | None = None
) -> dict:
    """Rack macro snapshots. action: "store" (save current macros as a
    variation), "recall" (restore variation `index`), "randomize" (randomize
    all macro knobs - instant sound-design exploration)."""
    return get_ableton_connection().send_command(
        "rack_variation",
        params(track_index=track_index, device_index=device_index, action=action, index=index),
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_simpler_playback_mode(track_index: int, device_index: int, mode: int) -> dict:
    """Set a Simpler device's playback mode: 0 = Classic, 1 = One-Shot, 2 = Slicing.
    Slicing chops the sample into playable slices, central to lofi/hip-hop
    sampling. Errors if the device at that index is not a Simpler."""
    return get_ableton_connection().send_command(
        "set_simpler_playback_mode",
        {"track_index": track_index, "device_index": device_index, "mode": mode},
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
def replace_simpler_sample(track_index: TrackIndex, device_index: DeviceIndex, path: str) -> dict:
    """Replace a Simpler sample with an absolute local audio-file path. Requires Live 12.4+
    AND runtime API support; otherwise fails before writing. Replaces musical content:
    confirm the target and preserve the original set. Readback is not an audio audition.
    """
    return get_ableton_connection().send_command(
        "replace_simpler_sample",
        {"track_index": track_index, "device_index": device_index, "path": path},
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_device_routing(track_index: int, device_index: int) -> dict:
    """Read a device's audio-input (sidechain) routing: current input type/channel
    and the available options. Only sidechain-capable devices (Compressor, Gate)
    have this. Use the options with set_device_routing to pick a sidechain source."""
    return get_ableton_connection().send_command(
        "get_device_routing", {"track_index": track_index, "device_index": device_index}
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_device_routing(track_index: int, device_index: int, field: str, display_name: str) -> dict:
    """Set a device's sidechain audio input. `field` is "input_routing_type" (the
    source track/return) or "input_routing_channel" (Pre FX / Post FX / Post Mixer);
    `display_name` is one of the options from get_device_routing. For ducking, also
    enable the device's Sidechain parameter via set_device_parameter."""
    return get_ableton_connection().send_command(
        "set_device_routing",
        {
            "track_index": track_index,
            "device_index": device_index,
            "field": field,
            "display_name": display_name,
        },
    )
