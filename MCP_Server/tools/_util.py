"""Shared helpers for tool modules."""


def params(**kw):
    """Build a wire-params dict, dropping None values (omitted optionals)."""
    return {k: v for k, v in kw.items() if v is not None}
