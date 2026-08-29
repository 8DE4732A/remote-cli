"""remote-cli: Shared SSH CLI tool for AI Agent and human co-piloting."""

try:
    from ._version import __version__, __version_tuple__
except ImportError:
    try:
        from importlib.metadata import version

        __version__ = version("remote-cli")
        __version_tuple__ = (0, 1, 0)
    except Exception:
        __version__ = "0.1.0"
        __version_tuple__ = (0, 1, 0)

__all__ = ["__version__", "__version_tuple__"]
