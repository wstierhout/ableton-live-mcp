"""Device loading and parameter control, incl. Master/Return chains."""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..connection import get_ableton_connection
from ._util import params


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def load_instrument_or_effect(ctx: Context, track_index: int, uri: str) -> str:
    """
    Load an instrument or effect onto a track using its URI.

    Parameters:
    - track_index: The index of the track to load the instrument on
    - uri: The URI of the instrument or effect to load (e.g., 'query:Synths#Instrument%20Rack:Bass:FileId_5116')
    """
    result = get_ableton_connection().send_command(
        "load_browser_item", {"track_index": track_index, "item_uri": uri}
    )
    # The Remote Script raises on failure, so a reply means it loaded.
    return f"Loaded '{result.get('item_name', uri)}' on track '{result.get('track_name', track_index)}'"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_device_parameters(ctx: Context, track_index: int, device_index: int) -> str:
    """List all parameters of a device on a track: names, values, ranges, display strings."""
    result = get_ableton_connection().send_command(
        "get_device_parameters", {"track_index": track_index, "device_index": device_index}
    )
    return json.dumps(result, indent=2)


def _set_param(command: str, prefix: str, **wire_params) -> str:
    r = get_ableton_connection().send_command(command, wire_params)
    return f"{prefix}{r.get('device')}: {r.get('parameter')} = {r.get('display', r.get('value'))}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_device_parameter(
    ctx: Context, track_index: int, device_index: int, parameter: str | int, value: float
) -> str:
    """Set a device parameter by name or index. Value is clamped to the parameter's min/max."""
    return _set_param(
        "set_device_parameter",
        "",
        track_index=track_index,
        device_index=device_index,
        parameter=parameter,
        value=value,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_device_enabled(ctx: Context, track_index: int, device_index: int, enabled: bool) -> str:
    """Enable or bypass a device on a track."""
    r = get_ableton_connection().send_command(
        "set_device_enabled",
        {"track_index": track_index, "device_index": device_index, "enabled": enabled},
    )
    return f"{r.get('device')} enabled={r.get('enabled')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def load_device_to_return(ctx: Context, return_index: int, item_uri: str) -> str:
    """Load a device/effect from the browser onto a return track."""
    r = get_ableton_connection().send_command(
        "load_device_to_return", {"return_index": return_index, "item_uri": item_uri}
    )
    return f"Loaded {r.get('item_name')} onto return {return_index}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def load_device_to_master(ctx: Context, item_uri: str) -> str:
    """Load a device/effect from the browser onto the Master/Main track (e.g. a limiter for mastering)."""
    r = get_ableton_connection().send_command("load_device_to_master", {"item_uri": item_uri})
    return f"Loaded {r.get('item_name')} onto master"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_master_device_parameters(ctx: Context, device_index: int) -> str:
    """List parameters of a device on the Master track."""
    return json.dumps(
        get_ableton_connection().send_command(
            "get_master_device_parameters", {"device_index": device_index}
        ),
        indent=2,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_master_device_parameter(
    ctx: Context, device_index: int, parameter: str | int, value: float
) -> str:
    """Set a parameter on a Master-track device by name or index."""
    return _set_param(
        "set_master_device_parameter",
        "master ",
        device_index=device_index,
        parameter=parameter,
        value=value,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_return_device_parameter(
    ctx: Context, return_index: int, device_index: int, parameter: str | int, value: float
) -> str:
    """Set a parameter on a Return-track device by name or index."""
    return _set_param(
        "set_return_device_parameter",
        f"return {return_index} ",
        return_index=return_index,
        device_index=device_index,
        parameter=parameter,
        value=value,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_return_device_parameters(ctx: Context, return_index: int, device_index: int) -> str:
    """List parameters of a device on a Return track: names, values, min/max,
    display strings. Call before set_return_device_parameter."""
    result = get_ableton_connection().send_command(
        "get_return_device_parameters",
        {"return_index": return_index, "device_index": device_index},
    )
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_rack_chains(ctx: Context, track_index: int, device_index: int) -> str:
    """List an Instrument/Effect Rack's chains and the devices inside each -
    previously unreachable nested devices. Use set_chain_device_parameter to
    control them."""

    r = get_ableton_connection().send_command(
        "get_rack_chains", {"track_index": track_index, "device_index": device_index}
    )
    return json.dumps(r, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_chain_device_parameter(
    ctx: Context,
    track_index: int,
    device_index: int,
    chain_index: int,
    chain_device_index: int,
    parameter: str | int,
    value: float,
) -> str:
    """Set a parameter on a device INSIDE a rack chain (indices from
    get_rack_chains). Values clamp to the parameter's native range."""
    return _set_param(
        "set_chain_device_parameter",
        "",
        track_index=track_index,
        device_index=device_index,
        chain_index=chain_index,
        chain_device_index=chain_device_index,
        parameter=parameter,
        value=value,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_drum_pads(ctx: Context, track_index: int, device_index: int) -> str:
    """List a Drum Rack's occupied pads (MIDI note, name, mute, solo)."""

    r = get_ableton_connection().send_command(
        "get_drum_pads", {"track_index": track_index, "device_index": device_index}
    )
    return json.dumps(r, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_drum_pad(
    ctx: Context,
    track_index: int,
    device_index: int,
    note: int,
    mute: bool | None = None,
    solo: bool | None = None,
    name: str | None = None,
) -> str:
    """Mute/solo/rename a single Drum Rack pad by its MIDI note (e.g. 36=kick).
    Per-pad muting = instant beat variations without touching the notes."""
    r = get_ableton_connection().send_command(
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

    return f"Pad {note}: {json.dumps(r)}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def rack_variation(
    ctx: Context, track_index: int, device_index: int, action: str, index: int | None = None
) -> str:
    """Rack macro snapshots. action: "store" (save current macros as a
    variation), "recall" (restore variation `index`), "randomize" (randomize
    all macro knobs - instant sound-design exploration)."""
    r = get_ableton_connection().send_command(
        "rack_variation",
        params(track_index=track_index, device_index=device_index, action=action, index=index),
    )
    return f"{action}: variations={r.get('variation_count')}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_simpler_playback_mode(ctx: Context, track_index: int, device_index: int, mode: int) -> str:
    """Set a Simpler device's playback mode: 0 = Classic, 1 = One-Shot, 2 = Slicing.
    Slicing chops the sample into playable slices, central to lofi/hip-hop
    sampling. Errors if the device at that index is not a Simpler."""
    r = get_ableton_connection().send_command(
        "set_simpler_playback_mode",
        {"track_index": track_index, "device_index": device_index, "mode": mode},
    )
    return f"Simpler '{r.get('device')}' playback mode: {r.get('playback_mode')}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_device_routing(ctx: Context, track_index: int, device_index: int) -> str:
    """Read a device's audio-input (sidechain) routing: current input type/channel
    and the available options. Only sidechain-capable devices (Compressor, Gate)
    have this. Use the options with set_device_routing to pick a sidechain source."""
    r = get_ableton_connection().send_command(
        "get_device_routing", {"track_index": track_index, "device_index": device_index}
    )
    return json.dumps(r, indent=2)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False))
def set_device_routing(
    ctx: Context, track_index: int, device_index: int, field: str, display_name: str
) -> str:
    """Set a device's sidechain audio input. `field` is "input_routing_type" (the
    source track/return) or "input_routing_channel" (Pre FX / Post FX / Post Mixer);
    `display_name` is one of the options from get_device_routing. For ducking, also
    enable the device's Sidechain parameter via set_device_parameter."""
    r = get_ableton_connection().send_command(
        "set_device_routing",
        {
            "track_index": track_index,
            "device_index": device_index,
            "field": field,
            "display_name": display_name,
        },
    )
    return f"'{r.get('device')}' {field}: {r.get(field)}"
