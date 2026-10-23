"""Ableton Live integration through the Model Context Protocol."""

__version__ = "1.8.1"

__all__ = ["AbletonConnection", "get_ableton_connection", "__version__"]


def __getattr__(name):
    # Preserve package-level imports without loading networking/the MCP stack in
    # isolated decoder workers, native-kernel tests or CLI version/help requests.
    if name in ("AbletonConnection", "get_ableton_connection"):
        from . import connection

        return getattr(connection, name)
    raise AttributeError(name)
