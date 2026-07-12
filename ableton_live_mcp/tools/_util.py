"""Shared helpers for tool modules."""


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
