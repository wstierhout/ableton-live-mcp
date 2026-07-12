"""Tolerant readers and the shared loader for Ableton's gzip-XML files
(.als, .adg, .adv).

Live stores most fields as ``<Foo Value="..."/>`` and its layout shifts between
versions, so every reader is best-effort: it returns a default rather than raising
on missing or malformed values. Shared by the offline `.als` and `.adg`/`.adv`
parsers.
"""

import gzip
import os
import zlib
from xml.etree import ElementTree as ET

# A real .als/.adg is a few MB gzipped, tens of MB expanded. The cap only exists
# so a crafted or corrupt file (gzip expands up to ~1000:1) cannot grow without
# bound in memory before the DOM multiplies it further.
MAX_XML_BYTES = 256 * 1024 * 1024


def load_gz_xml(path, noun=".als"):
    """Open, decompress, and parse an Ableton gzip-XML file.

    Returns ``(root, expanded_path, None)`` on success or ``(None, expanded_path,
    error)`` with a user-facing error string. The root is guaranteed to be an
    ``<Ableton>`` element.
    """
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None, path, f"No file at {path}. Pass the full path to a {noun} file."
    try:
        with gzip.open(path, "rb") as f:
            data = f.read(MAX_XML_BYTES + 1)
        if len(data) > MAX_XML_BYTES:
            return (
                None,
                path,
                f"{path} decompresses past {MAX_XML_BYTES // (1024 * 1024)} MB; "
                f"refusing to parse it (corrupt or not a real {noun} file).",
            )
        root = ET.fromstring(data)
    except (gzip.BadGzipFile, EOFError, zlib.error):
        return None, path, f"{path} is not a valid gzip-compressed {noun} file."
    except ET.ParseError as e:
        return None, path, f"Could not parse the XML in {path}: {e}"
    except OSError as e:
        return None, path, f"Could not read {path}: {e}"
    if root.tag != "Ableton":
        return (
            None,
            path,
            f"{path} parses as XML but its root element is <{root.tag}>, "
            f"not <Ableton> — not an Ableton {noun} file.",
        )
    return root, path, None


def _val(elem, default=None):
    """Read an element's ``Value`` attribute, falling back to its text."""
    if elem is None:
        return default
    v = elem.get("Value")
    if v is not None:
        return v
    if elem.text and elem.text.strip():
        return elem.text.strip()
    return default


def _fnum(elem, default=None):
    try:
        return float(_val(elem))
    except (TypeError, ValueError):
        return default


def _inum(elem, default=None):
    v = _val(elem)
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def _flag(elem, default=False):
    v = _val(elem)
    return default if v is None else v.strip().lower() == "true"


def _afloat(elem, name, default=None):
    """Read a named attribute as a float (used for MidiNoteEvent Time/Velocity)."""
    try:
        return float(elem.get(name))
    except (TypeError, ValueError):
        return default


def _aint(elem, name, default=None):
    v = elem.get(name)
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default
