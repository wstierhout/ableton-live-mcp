"""Curated knowledge base for common Ableton native devices.

`get_device_parameters` reads a device's live parameter list (real names and
ranges), but it cannot tell the model which parameters matter, sensible starting
values, or that a knob is perceptual rather than linear. This static KB fills that
gap for the most-used devices. The key distinction is the `unit` field: `linear`
parameters map value proportionally (0.5 is halfway), while `curve` parameters are
perceptual (0.5 is NOT half the audible amount) - always read the display value
after setting a `curve` parameter rather than assuming proportionality.
"""

import json

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp

# Each device: a short list of the parameters that matter, with a friendly note,
# a unit ("linear" | "curve" | "db" | "hz" | "ms" | "ratio" | "enum"), and a
# sensible starting value or range. Names match Live's parameter names.
DEVICE_KB = {
    "eq eight": {
        "role": "Surgical/tonal EQ, 8 bands.",
        "params": [
            {
                "name": "1 Frequency A",
                "unit": "hz",
                "note": "Band 1 frequency; high-pass rumble around 30-40 Hz.",
            },
            {
                "name": "1 Gain A",
                "unit": "db",
                "note": "Band gain, -15 to +15 dB. Small moves (2-3 dB) go a long way.",
            },
            {"name": "1 Resonance A", "unit": "curve", "note": "Q / bandwidth; higher = narrower."},
        ],
        "tips": "For lofi, roll off highs above ~8 kHz and lows below ~40 Hz. Cut before you boost.",
    },
    "compressor": {
        "role": "Dynamics compressor with sidechain input.",
        "params": [
            {
                "name": "Threshold",
                "unit": "db",
                "note": "Level where compression starts; lower = more compression.",
            },
            {
                "name": "Ratio",
                "unit": "ratio",
                "note": "Amount of gain reduction (2:1 gentle, 4:1 firm, inf = limiting).",
            },
            {
                "name": "Attack",
                "unit": "ms",
                "note": "How fast it clamps; fast kills transients, slow keeps punch.",
            },
            {
                "name": "Release",
                "unit": "ms",
                "note": "How fast it recovers; sync to tempo for pumping.",
            },
            {"name": "Dry/Wet", "unit": "linear", "note": "Parallel compression when below 100%."},
        ],
        "tips": "For sidechain ducking, set the input routing (set_device_routing) to the kick, enable the Sidechain section, fast attack, release ~ an 8th note.",
    },
    "glue compressor": {
        "role": "Bus/master glue compressor (SSL-style).",
        "params": [
            {"name": "Threshold", "unit": "db", "note": "Onset of compression."},
            {"name": "Ratio", "unit": "enum", "note": "2, 4, or 10."},
            {"name": "Attack", "unit": "ms", "note": "0.01 to 30 ms; 10-30 keeps transients."},
            {"name": "Release", "unit": "ms", "note": "Auto is great for a mix bus."},
            {"name": "Makeup", "unit": "db", "note": "Compensate for the level lost."},
        ],
        "tips": "On the master, aim for 1-2 dB of gain reduction with slow attack and Auto release.",
    },
    "reverb": {
        "role": "Algorithmic reverb.",
        "params": [
            {"name": "DecayTime", "unit": "ms", "note": "Tail length."},
            {"name": "Room Size", "unit": "curve", "note": "Perceived space; not linear."},
            {
                "name": "Dry/Wet",
                "unit": "linear",
                "note": "Put reverb on a return and keep this at 100% there.",
            },
        ],
        "tips": "Use a return track for reverb and send to it, rather than an insert, so several tracks share one space.",
    },
    "delay": {
        "role": "Tempo-syncable delay.",
        "params": [
            {"name": "L 16th", "unit": "enum", "note": "Left delay time in 16ths when synced."},
            {
                "name": "Feedback",
                "unit": "linear",
                "note": "Number of repeats; high values self-oscillate.",
            },
            {"name": "Dry/Wet", "unit": "linear", "note": "Blend."},
        ],
        "tips": "A dotted-8th sync is the classic rhythmic delay.",
    },
    "auto filter": {
        "role": "Resonant filter with LFO/envelope.",
        "params": [
            {
                "name": "Frequency",
                "unit": "curve",
                "note": "Cutoff; perceptual, sweep it for movement.",
            },
            {"name": "Resonance", "unit": "curve", "note": "Emphasis at the cutoff."},
        ],
        "tips": "A slow low-pass sweep on a pad adds motion; automate Frequency.",
    },
    "saturator": {
        "role": "Warmth/distortion.",
        "params": [
            {
                "name": "Drive",
                "unit": "curve",
                "note": "Amount of saturation; 0.5 is NOT half the grit.",
            },
            {"name": "Dry/Wet", "unit": "linear", "note": "Blend the dirt in."},
        ],
        "tips": "A little drive on drums or bass adds analog warmth for lofi.",
    },
    "utility": {
        "role": "Gain, width, mono, DC filter.",
        "params": [
            {"name": "Gain", "unit": "db", "note": "Clean level trim."},
            {
                "name": "Stereo Width",
                "unit": "linear",
                "note": "0% = mono, 100% = original, up to 400%.",
            },
        ],
        "tips": "Mono the low end (narrow Width, or Bass Mono) to keep the mix tight.",
    },
    "limiter": {
        "role": "Brickwall limiter for the master.",
        "params": [
            {
                "name": "Gain",
                "unit": "db",
                "note": "Drive into the ceiling; more = louder + more limiting.",
            },
            {
                "name": "Ceiling",
                "unit": "db",
                "note": "Output ceiling; -1 dB is a safe master target.",
            },
        ],
        "tips": "Set Ceiling to -1 dB and raise Gain until you get ~2-3 dB of reduction on peaks.",
    },
}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def describe_device(ctx: Context, device_name: str) -> str:
    """Return curated guidance for a common Ableton native device: which parameters
    matter, their units (note `curve` = perceptual, not proportional), sensible
    values, and mixing tips. `device_name` is case-insensitive (e.g. "EQ Eight",
    "Compressor", "Glue Compressor", "Reverb", "Auto Filter", "Saturator",
    "Utility", "Limiter", "Delay"). Pair with get_device_parameters for the live
    indices and ranges."""
    key = device_name.strip().lower()
    entry = DEVICE_KB.get(key)
    if entry is None:
        matches = [k for k in DEVICE_KB if key in k or k in key]
        if len(matches) == 1:
            key, entry = matches[0], DEVICE_KB[matches[0]]
        else:
            raise ValueError(
                f"No knowledge-base entry for '{device_name}'. Known: {sorted(DEVICE_KB)}"
            )
    return json.dumps({"device": key, **entry}, indent=2)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_known_devices(ctx: Context) -> str:
    """List the devices that describe_device has curated guidance for."""
    return json.dumps({"devices": sorted(DEVICE_KB)}, indent=2)
