"""Compatibility utilities."""

from typing import IO, AnyStr

__all__ = ["isatty", "want_bytes", "want_str"]


def want_bytes(s: AnyStr) -> bytes:
    """Convert string to bytes."""
    if isinstance(s, str):
        return s.encode()
    return s


def want_str(s: AnyStr) -> str:
    """Convert bytes to string."""
    if isinstance(s, bytes):
        return s.decode()
    return s


def isatty(fh: IO) -> bool:
    """Return True if fh has a controlling terminal.

    Notes:
        Use with e.g. `sys.stdin`.
    """
    try:
        return fh.isatty()
    except AttributeError:
        return False
