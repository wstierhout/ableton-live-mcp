"""Shared helpers and JSON-schema types for tool modules."""

from typing import Annotated

from pydantic import Field

# FastMCP preserves Pydantic metadata from ``Annotated`` in each tool's input
# schema.  Keep the conventions here so agents see the same index, unit, and
# range guidance on every tool instead of having to infer it from prose.
TrackIndex = Annotated[
    int,
    Field(ge=0, description="Zero-based index of a regular track in the Live set."),
]
ClipIndex = Annotated[
    int,
    Field(ge=0, description="Zero-based Session-view clip-slot index on the track."),
]
SceneIndex = Annotated[
    int,
    Field(ge=0, description="Zero-based Session-view scene (row) index."),
]
ReturnIndex = Annotated[
    int,
    Field(ge=0, description="Zero-based return-track index; 0 is Return A."),
]
DeviceIndex = Annotated[
    int,
    Field(ge=0, description="Zero-based device position in the target track's device chain."),
]
TrackInsertIndex = Annotated[
    int,
    Field(
        ge=-1,
        description="Insertion position among regular tracks; -1 appends at the end.",
    ),
]
ColorIndex = Annotated[
    int,
    Field(ge=0, le=69, description="Color index in Live's 70-color palette (0-69)."),
]
PanValue = Annotated[
    float,
    Field(ge=-1, le=1, description="Pan position from -1.0 (left) to 1.0 (right)."),
]
CrossfadeAssignment = Annotated[
    int,
    Field(ge=0, le=2, description="Crossfader assignment: 0 is A, 1 is none, 2 is B."),
]
ArrangementBeat = Annotated[
    float,
    Field(ge=0, description="Position in beats from the start of the Arrangement."),
]
OptionalClipBeat = Annotated[
    float | None,
    Field(ge=0, description="Optional position in beats from the start of the clip."),
]
PositiveBeatLength = Annotated[
    float,
    Field(gt=0, description="Positive duration or region length in beats."),
]
DeviceParameter = Annotated[
    str | int,
    Field(
        description=(
            "Parameter name or zero-based parameter index returned by the matching "
            "get_*_device_parameters tool."
        )
    ),
]
DeviceParameterValue = Annotated[
    float,
    Field(
        description=(
            "Value in the parameter's native range; the server clamps it to the min/max "
            "reported by the matching get_*_device_parameters tool."
        )
    ),
]
BrowserItemUri = Annotated[
    str,
    Field(description="Loadable browser-item URI returned by search_browser."),
]
BrowserPath = Annotated[
    str,
    Field(description='Slash-separated Live browser path, such as "drums/acoustic".'),
]
DisplayName = Annotated[str, Field(description="New display name for the Live object.")]
OptionalDisplayName = Annotated[
    str | None,
    Field(description="Optional display name; omit it to keep Live's generated name."),
]
ToggleState = Annotated[
    bool,
    Field(description="True turns the requested state on; false turns it off."),
]
OptionalToggleState = Annotated[
    bool | None,
    Field(description="Optional state change: true turns it on, false turns it off."),
]
TimeSignatureNumerator = Annotated[
    int,
    Field(ge=1, description="Number of beats per bar, such as 4 in 4/4."),
]
TimeSignatureDenominator = Annotated[
    int,
    Field(ge=1, description="Beat-note denominator, such as 4 in 4/4 or 8 in 6/8."),
]


def params(**kw):
    """Build a wire-params dict, dropping None values (omitted optionals)."""
    return {k: v for k, v in kw.items() if v is not None}


def keyed_by_name(tracks):
    """Key tracks by name, disambiguating duplicates as 'name #2', 'name #3', so a
    diff does not silently collapse same-named tracks to the last one."""
    seen = {}
    out = {}
    for t in tracks:
        name = t["name"]
        seen[name] = seen.get(name, 0) + 1
        out[name if seen[name] == 1 else f"{name} #{seen[name]}"] = t
    return out
