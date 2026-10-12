"""Open-source control for Cube26 / iota "Reos Lite" Bluetooth LE lights."""

from . import protocol  # noqa: F401

__all__ = ["protocol", "ReosLite", "scan"]


def __getattr__(name):
    # Lazy so the pure protocol module works without bleak installed.
    if name in ("ReosLite", "scan"):
        from . import client
        return getattr(client, name)
    raise AttributeError(name)
